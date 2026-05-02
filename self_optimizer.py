"""
Self-Optimizer — 自动调参模块

基于复盘数据自动调整 Pipeline 拒绝阈值：
- 收集被拒绝决策的24h价格结果
- 统计每个拒绝理由的"判断正确率"
- 当某拒绝理由连续误判超过阈值，自动放宽参数
- 当某拒绝理由持续正确，适当收紧参数

使用方式：
    python self_optimizer.py          # 诊断报告
    python self_optimizer.py --apply  # 诊断 + 自动调参
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from market import Market
    from decision_memory import DecisionMemory
    from config import BASE_DIR, STATE_PATH
except ImportError:
    print("请在 trading-core 目录下运行")
    sys.exit(1)


# ============================================================
# 调参阈值
# ============================================================
MIN_SAMPLE = 10          # 最少样本量才开始调参
CORRECT_RATE_TIGHTEN = 0.85   # 正确率 > 85% → 可收紧
CORRECT_RATE_LOOSEN = 0.40    # 正确率 < 40% → 需放宽
ADJUST_STEP = 0.20           # 每次调整幅度 20%
ROLLING_WINDOW = 50           # 滚动窗口大小


# ============================================================
# 追踪的拒绝原因
# ============================================================
# 每个原因对应一个 (config_key, numeric_key) 用来找阈值
VETO_REASONS = {
    # entry_veto reasons
    "funding_rate": {
        "type": "entry_veto",
        "pattern": "funding=",
        "description": "资金费率阈值",
        "threshold_key": "funding_pct",
        "direction": "abs_gt",   # 绝对值大于阈值
        "default": 0.05,
        "min": 0.03,
        "max": 0.15,
    },
    "taker_trend": {
        "type": "entry_veto",
        "pattern": "taker trend=",
        "description": "Taker主动卖出趋势",
        "threshold_key": "taker_trend_pct",
        "direction": "lt",
        "default": -5.0,
        "min": -20.0,
        "max": -2.0,
    },
    "change_4h": {
        "type": "entry_veto",
        "pattern": "4h change=",
        "description": "4h涨跌幅过大",
        "threshold_key": "change_4h_pct",
        "direction": "abs_gt",
        "default": 25.0,
        "min": 15.0,
        "max": 50.0,
    },
    "change_24h": {
        "type": "entry_veto",
        "pattern": "24h change=",
        "description": "24h涨跌幅过大",
        "threshold_key": "change_24h_pct",
        "direction": "abs_gt",
        "default": 50.0,
        "min": 30.0,
        "max": 100.0,
    },
    "retail_lsr": {
        "type": "entry_veto",
        "pattern": "retail LSR=",
        "description": "全网多空比过热",
        "threshold_key": "lsr_pct",
        "direction": "gt",
        "default": 1.7,
        "min": 1.3,
        "max": 2.5,
    },
    "taker_ratio": {
        "type": "entry_veto",
        "pattern": "taker ratio=",
        "description": "Taker买卖比过热",
        "threshold_key": "taker_ratio",
        "direction": "gt",
        "default": 1.8,
        "min": 1.5,
        "max": 3.0,
    },
    # env_reject
    "env_reject": {
        "type": "env_reject",
        "pattern": "env_reject",
        "description": "环境综合评分不足",
        "threshold_key": "env_min_score",
        "direction": "lt",
        "default": 3.0,
        "min": 1.0,
        "max": 5.0,
    },
    # quality_reject
    "quality_reject": {
        "type": "quality_reject",
        "pattern": "quality_reject",
        "description": "入场质量不足",
        "threshold_key": "quality_min_score",
        "direction": "lt",
        "default": 50.0,
        "min": 30.0,
        "max": 70.0,
    },
}

# Hard tag 追踪（价格过热等）
HARD_TAGS = {
    "price_overheated": {
        "description": "24h涨幅过大",
        "direction": "against",
    },
    "funding_hot": {
        "description": "资金费率过热",
        "direction": "against",
    },
}


# ============================================================
# 判断"被拒绝的决策"24h后方向是否对你有利
# ============================================================
def would_have_won(direction: str, entry_price: float,
                   snapshot: dict, review_price: float) -> Optional[bool]:
    """
    判断如果当时开仓了，这笔交易会不会赢。
    direction: 'long' or 'short'
    entry_price: 原决策的入场价
    snapshot: 市场快照（含当时的情绪/理由）
    review_price: 24h后的价格
    """
    if direction not in ("long", "short") or entry_price <= 0:
        return None

    direction_correct = (
        (direction == "long" and review_price > entry_price) or
        (direction == "short" and review_price < entry_price)
    )
    return direction_correct


# ============================================================
# 解析拒绝原因
# ============================================================
def parse_rejection_reason(reasoning: str) -> dict:
    """从 reasoning 字符串里解析出具体的拒绝原因"""
    result = {}

    for key, meta in VETO_REASONS.items():
        if meta["pattern"] in reasoning:
            result[key] = {
                "type": meta["type"],
                "reasoning": reasoning,
            }
            break

    # Hard tags
    for tag in HARD_TAGS:
        if f"hard_tags={tag}" in reasoning or tag in reasoning:
            result[tag] = {
                "type": "score_reject",
                "tag": tag,
                "reasoning": reasoning,
            }

    # env_reject / quality_reject
    if "env_reject" in reasoning:
        result["env_reject"] = {"type": "env_reject", "reasoning": reasoning}
    if "quality_reject" in reasoning:
        result["quality_reject"] = {"type": "quality_reject", "reasoning": reasoning}

    return result


# ============================================================
# 核心分析
# ============================================================
def analyze_rejections(decisions: list[dict]) -> dict:
    """
    分析所有已复盘的决策，输出每个拒绝原因的正确率统计
    """
    stats = {}  # reason_key -> {"correct": N, "wrong": M, "decisions": [...]}

    for d in decisions:
        action = d.get("action", "")
        reasoning = d.get("reasoning", "") or ""
        context_json = d.get("context_json", "{}")
        try:
            context = json.loads(context_json)
        except Exception:
            context = {}

        snapshot = context.get("snapshot", {})
        direction = d.get("direction", "long")
        entry_price = _num(d.get("entry_price"), 0) or 0
        review_price = _num(d.get("review_price"), 0) or 0

        if entry_price <= 0 or review_price <= 0:
            continue

        # skip opened trades for now (they have their own outcome)
        if action == "opened":
            continue

        win = would_have_won(direction, entry_price, snapshot, review_price)
        if win is None:
            continue

        # 解析拒绝原因
        reasons = parse_rejection_reason(reasoning)
        for reason_key, meta in reasons.items():
            if reason_key not in stats:
                stats[reason_key] = {"correct": 0, "wrong": 0, "total": 0}

            stats[reason_key]["total"] += 1
            if win:
                stats[reason_key]["correct"] += 1
            else:
                stats[reason_key]["wrong"] += 1

    return stats


def compute_accuracy(stats: dict) -> dict:
    """计算每个拒绝原因的正确率"""
    result = {}
    for key, s in stats.items():
        total = s["total"]
        if total < MIN_SAMPLE:
            result[key] = {"accuracy": None, "sample": total, "n": MIN_SAMPLE, "status": "样本不足"}
        else:
            acc = s["correct"] / total
            if acc >= CORRECT_RATE_TIGHTEN:
                status = "可收紧"
            elif acc <= CORRECT_RATE_LOOSEN:
                status = "需放宽"
            else:
                status = "正常"
            result[key] = {"accuracy": round(acc * 100, 1), "correct": s["correct"], "total": total, "status": status}
    return result


def suggest_adjustments(accuracy_report: dict) -> dict:
    """根据正确率生成调参建议"""
    suggestions = {}
    current = load_current_thresholds()

    for key, report in accuracy_report.items():
        if report["status"] == "样本不足" or report["status"] == "正常":
            continue

        meta = VETO_REASONS.get(key)
        if not meta:
            continue

        current_val = current.get(key, meta["default"])

        if report["status"] == "需放宽":
            # 放宽：增大阈值允许更多交易
            new_val = current_val * (1 + ADJUST_STEP)
            new_val = min(new_val, meta["max"])
            suggestions[key] = {
                "action": "loosen",
                "reason": f"正确率仅{report['accuracy']}%，连续误判",
                "old": round(current_val, 4),
                "new": round(new_val, 4),
                "meta": meta,
            }
        elif report["status"] == "可收紧":
            # 收紧：减小阈值过滤更多噪音
            new_val = current_val * (1 - ADJUST_STEP)
            new_val = max(new_val, meta["min"])
            suggestions[key] = {
                "action": "tighten",
                "reason": f"正确率{report['accuracy']}%，判断精准",
                "old": round(current_val, 4),
                "new": round(new_val, 4),
                "meta": meta,
            }

    return suggestions


# ============================================================
# 阈值读写
# ============================================================
def load_current_thresholds() -> dict:
    """从 state.json 加载当前阈值"""
    state_path = Path.home() / ".hermes" / "trading_core" / "state.json"
    if state_path.exists():
        try:
            with open(state_path) as f:
                state = json.load(f)
            return state.get("veto_thresholds", {})
        except Exception:
            pass
    return {}


def save_thresholds(thresholds: dict):
    """保存阈值到 state.json"""
    state_path = Path.home() / ".hermes" / "trading_core" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)

    state = {}
    if state_path.exists():
        try:
            with open(state_path) as f:
                state = json.load(f)
        except Exception:
            pass

    state["veto_thresholds"] = thresholds
    state["last_optimizer_run"] = datetime.now(timezone.utc).isoformat()

    with open(state_path, "w") as f:
        json.dump(state, f, indent=2)

    print(f"✅ 阈值已保存到 {state_path}")


# ============================================================
# 工具
# ============================================================
def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ============================================================
# 主流程
# ============================================================
def run(dry_run: bool = True) -> dict:
    print("=" * 60)
    print("Self-Optimizer  自动调参诊断")
    print("=" * 60)

    # 1. 收集已复盘的决策
    print("\n[1] 收集已复盘的决策...")
    decisions = DecisionMemory.review_due(limit=200)
    if not decisions:
        print("  无已复盘决策，先跑一段时间积累数据")
        return {"ok": False, "reason": "no_reviewed_decisions"}

    print(f"  已复盘决策: {len(decisions)} 条")

    # 2. 分析正确率
    print("\n[2] 分析每个拒绝原因的正确率...")
    stats = analyze_rejections(decisions)
    if not stats:
        print("  无有效拒绝数据（可能是opened决策）")
        return {"ok": False, "reason": "no_rejection_data"}

    accuracy = compute_accuracy(stats)

    print(f"\n  {'拒绝原因':<20} {'正确率':>8} {'样本':>6} {'状态':>10}")
    print(f"  {'-'*50}")
    for key, report in accuracy.items():
        meta = VETO_REASONS.get(key, HARD_TAGS.get(key, {}))
        desc = meta.get("description", key) if meta else key
        acc_str = f"{report['accuracy']}%" if report["accuracy"] is not None else "N/A"
        sample_str = f"{report.get('total', 0)}/{report.get('n', MIN_SAMPLE)}"
        print(f"  {desc:<20} {acc_str:>8} {sample_str:>6} {report['status']:>10}")

    # 3. 生成调参建议
    print("\n[3] 调参建议...")
    suggestions = suggest_adjustments(accuracy)

    if not suggestions:
        print("  无需调整，所有阈值在正常范围")
        return {"ok": True, "accuracy": accuracy, "suggestions": {}}

    for key, s in suggestions.items():
        meta = s["meta"]
        desc = meta.get("description", key)
        emoji = "🔴" if s["action"] == "loosen" else "🟢"
        print(f"  {emoji} {desc}")
        print(f"     当前值: {s['old']} → 新值: {s['new']}")
        print(f"     原因: {s['reason']}")

    # 4. 应用
    if dry_run:
        print(f"\n⚠️  [dry-run] 使用 --apply 来实际保存新阈值")
    else:
        current = load_current_thresholds()
        for key, s in suggestions.items():
            current[key] = s["new"]
        save_thresholds(current)

    return {"ok": True, "accuracy": accuracy, "suggestions": suggestions}


# ============================================================
# CLI
# ============================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Self-Optimizer 自动调参")
    parser.add_argument("--apply", action="store_true", help="应用调参建议（不加则只诊断）")
    args = parser.parse_args()

    result = run(dry_run=not args.apply)
    if not result["ok"]:
        print(f"\n无法运行: {result.get('reason')}")
