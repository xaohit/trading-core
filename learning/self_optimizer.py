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
    from db.connection import get_db, init_db
except ImportError:
    from market import Market
    from decision_memory import DecisionMemory
    from config import BASE_DIR, STATE_PATH
    from db.connection import get_db, init_db


# ============================================================
# 调参阈值
# ============================================================
MIN_SAMPLE = 10          # 最少样本量才开始调参
MIN_REGIME_SAMPLE = 5    # per-regime 分析最低样本
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
    "long_taker_trend": {
        "type": "entry_veto",
        "pattern": "long taker trend=",
        "description": "Taker主动卖出趋势",
        "threshold_key": "long_taker_trend_pct",
        "direction": "lt",
        "default": -5.0,
        "min": -20.0,
        "max": -2.0,
    },
    "short_taker_trend": {
        "type": "entry_veto",
        "pattern": "short taker trend=",
        "description": "Taker active buying against shorts",
        "threshold_key": "short_taker_trend_pct",
        "direction": "gt",
        "default": 5.0,
        "min": 2.0,
        "max": 20.0,
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

        threshold_key = meta["threshold_key"]
        current_val = current.get(threshold_key, meta["default"])

        if report["status"] == "需放宽":
            # 放宽：增大阈值允许更多交易
            new_val = current_val * (1 + ADJUST_STEP)
            new_val = min(new_val, meta["max"])
            suggestions[key] = {
                "action": "loosen",
                "reason": f"正确率仅{report['accuracy']}%，连续误判",
                "old": round(current_val, 4),
                "new": round(new_val, 4),
                "threshold_key": threshold_key,
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
                "threshold_key": threshold_key,
                "meta": meta,
            }

    return suggestions


# ============================================================
# 阈值读写
# ============================================================
def load_current_thresholds() -> dict:
    """从 state.json 加载当前阈值"""
    state_path = STATE_PATH
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
    state_path = STATE_PATH
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
# Per-Regime 胜率分析
# ============================================================
def analyze_per_regime(decisions: list[dict]) -> dict:
    """
    按市场状态分组，统计各策略在不同 regime 下的胜率。
    输出：
        {
          "trending": {
            "neg_funding_long": {"wins": 3, "total": 5, "win_rate": 0.60, "avg_pnl": 0.08},
            ...
          },
          "ranging": {...},
          ...
        }
    """
    import json
    buckets: dict = {}

    for d in decisions:
        market_state_str = d.get("market_state") or "{}"
        try:
            ms = json.loads(market_state_str) if isinstance(market_state_str, str) else market_state_str
        except Exception:
            ms = {}

        # 归一化 regime 名称（兼容新旧格式）
        state = ms.get("state", "unknown")
        if state == "trending":
            trend_dir = ms.get("trend_direction", "neutral")
            regime_key = f"trending_{trend_dir}"
        elif state == "volatile":
            regime_key = "volatile"
        elif state == "ranging":
            regime_key = "ranging"
        else:
            regime_key = "unknown"

        signal_type = d.get("signal_type", "unknown")
        pnl_pct = _num(d.get("pnl_pct"), 0) or 0
        action = d.get("action", "")
        direction = d.get("direction", "")

        # 判断盈亏（opened 决策没有 pnl，以后的 decision_outcomes 为准）
        is_win = (pnl_pct > 0) if pnl_pct != 0 else None

        # 从 decision_outcomes 补充（opened 决策）
        if is_win is None and d.get("id"):
            outcome = _get_outcome(d["id"])
            if outcome is not None:
                is_win = outcome

        if regime_key not in buckets:
            buckets[regime_key] = {}
        bucket = buckets[regime_key]

        if signal_type not in bucket:
            bucket[signal_type] = {"wins": 0, "total": 0, "pnl_sum": 0.0}

        if is_win is not None:
            bucket[signal_type]["total"] += 1
            bucket[signal_type]["wins"] += 1 if is_win else 0
            bucket[signal_type]["pnl_sum"] += pnl_pct

    # 汇总统计
    result = {}
    for regime, strategies in buckets.items():
        result[regime] = {}
        for sig, s in strategies.items():
            total = s["total"]
            result[regime][sig] = {
                "wins": s["wins"],
                "total": total,
                "win_rate": round(s["wins"] / total, 4) if total > 0 else 0,
                "avg_pnl": round(s["pnl_sum"] / total, 4) if total > 0 else 0,
            }
    return result


def _get_outcome(snapshot_id: int) -> bool | None:
    """从 decision_outcomes 查 24h 结果"""
    try:
        init_db()
        conn = get_db()
        c = conn.cursor()
        c.execute(
            "SELECT direction_correct FROM decision_outcomes WHERE snapshot_id=? LIMIT 1",
            (snapshot_id,),
        )
        row = c.fetchone()
        if row is None:
            return None
        return bool(row[0])
    except Exception:
        return None


def regime_weight_suggestions(per_regime: dict) -> dict:
    """
    根据 per-regime 胜率生成策略权重调整建议。
    返回 {regime: {strategy: {"current": 0.5, "suggested": 0.8, "reason": "..."}}}
    """
    # 默认权重（来自 strategy_router DEFAULT_WEIGHTS）
    defaults = {
        "trending_up": {
            "neg_funding_long": 1.0,
            "pos_funding_short": 0.0,
            "crash_bounce_long": 0.8,
            "pump_short": 0.0,
        },
        "trending_down": {
            "neg_funding_long": 0.0,
            "pos_funding_short": 1.0,
            "crash_bounce_long": 0.0,
            "pump_short": 0.8,
        },
        "ranging": {
            "neg_funding_long": 1.0,
            "pos_funding_short": 1.0,
            "crash_bounce_long": 1.0,
            "pump_short": 1.0,
        },
    }

    suggestions = {}
    for regime, strategies in per_regime.items():
        if regime == "unknown":
            continue
        defaults_for_regime = defaults.get(regime, {})
        regime_suggestions = {}
        for sig, stats in strategies.items():
            if stats["total"] < MIN_REGIME_SAMPLE:
                continue
            current_w = defaults_for_regime.get(sig, 0.5)
            win_rate = stats["win_rate"]
            # 胜率 > 60% → 建议加权重；胜率 < 40% → 建议降权重
            if win_rate >= 0.60:
                suggested_w = min(current_w * 1.3, 1.0)
                reason = f"胜率{win_rate:.0%}表现好，建议加权重"
            elif win_rate <= 0.35:
                suggested_w = max(current_w * 0.5, 0.0)
                reason = f"胜率{win_rate:.0%}表现差，建议降权重"
            else:
                continue  # 正常范围不动
            regime_suggestions[sig] = {
                "current": current_w,
                "suggested": round(suggested_w, 3),
                "win_rate": win_rate,
                "n": stats["total"],
                "reason": reason,
            }
        if regime_suggestions:
            suggestions[regime] = regime_suggestions
    return suggestions


# ============================================================
# 主流程
# ============================================================
def run(dry_run: bool = True) -> dict:
    print("=" * 60)
    print("Self-Optimizer  自动调参诊断")
    print("=" * 60)

    # 1. 收集已复盘的决策
    print("\n[1] 收集已复盘的决策...")
    DecisionMemory.review_due(limit=200)
    decisions = DecisionMemory.reviewed_decisions(limit=ROLLING_WINDOW)
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

    # 4. Per-regime 胜率分析（Phase 4 新增）
    print(f"\n[4] Per-Regime 策略胜率分析...")
    per_regime = analyze_per_regime(decisions)
    if per_regime:
        for regime, strategies in per_regime.items():
            print(f"\n  【{regime}】")
            has_data = False
            for sig, stats in strategies.items():
                n = stats["total"]
                if n < MIN_REGIME_SAMPLE:
                    continue
                has_data = True
                wr = stats["win_rate"]
                bar = "█" * int(wr * 10) + "░" * (10 - int(wr * 10))
                print(f"    {sig:<22} {bar} {wr:.0%} ({stats['wins']}/{n})  avg={stats['avg_pnl']:+.2f}%")
            if not has_data:
                print(f"    样本不足（每策略需>{MIN_REGIME_SAMPLE}笔）")
        # 权重建议
        weight_sugs = regime_weight_suggestions(per_regime)
        if weight_sugs:
            print(f"\n  权重调整建议：")
            for regime, sigs in weight_sugs.items():
                print(f"  【{regime}】")
                for sig, sug in sigs.items():
                    emoji = "🟢" if sug["suggested"] > sug["current"] else "🔴"
                    print(f"    {emoji} {sig}: {sug['current']:.1f} → {sug['suggested']:.1f} ({sug['reason']})")
    else:
        print("  样本不足，无法分析")

    # 5. 应用
    if dry_run:
        print(f"\n⚠️  [dry-run] 使用 --apply 来实际保存新阈值")
    else:
        current = load_current_thresholds()
        for key, s in suggestions.items():
            current[s["threshold_key"]] = s["new"]
        save_thresholds(current)

        # 5b. 保存 regime 权重到 state.json（Phase 4）
        from strategy_router import StrategyRouter
        from market_regime import Regime
        # 加载当前已保存的 regime_weights（不覆盖全局，只更新有建议的策略）
        try:
            import json as _json
            state_path = Path.home() / ".hermes/trading_core/state.json"
            if state_path.exists():
                state_data = _json.loads(state_path.read_text())
            else:
                state_data = {}
            existing = state_data.get("regime_weights", {})
            # 合并：旧的保留，有新建议的覆盖
            for regime_key, sigs in weight_sugs.items():
                if regime_key not in existing:
                    existing[regime_key] = {}
                for sig, sug in sigs.items():
                    existing[regime_key][sig] = sug["suggested"]
            state_data["regime_weights"] = existing
            state_path.write_text(_json.dumps(state_data, indent=2, ensure_ascii=False))
            print(f"\n✅ regime_weights 已保存到 state.json")
        except Exception as e:
            print(f"\n⚠️  regime_weights 保存失败: {e}")

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
