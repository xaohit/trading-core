"""
Phase 6 — Reflection Engine: Failure Archive + Adaptive Strategy Weights

Components:
1. FailureArchive: Auto-tags stopped-out trades with root-cause analysis
2. StrategyWeighter: Dynamic strategy prioritization based on recent performance
3. RuleReflector: Suggests hard-entry thresholds from frequent failure tags

Expose:
    from reflection import FailureArchive, StrategyWeighter, RuleReflector

    # Archive a failed trade with tags
    tags = FailureArchive.analyze_failure(trade_data, entry_snapshot, exit_snapshot)
    FailureArchive.archive(trade_id, tags, reason)

    # Get adaptive weights for strategies
    weights = StrategyWeighter.get_weights()  # {strategy: weight}

    # Get rule suggestions from failure patterns
    suggestions = RuleReflector.get_suggestions()
"""
import json
import time
from typing import Any

try:
    from .config import STRATEGY_CONFIGS
    from .db.connection import get_db, init_db
    from .market import Market
except ImportError:
    from config import STRATEGY_CONFIGS
    from db.connection import get_db, init_db
    from market import Market


# === Failure Tag Definitions ===
# Each tag has a human-readable description and a suggested action
FAILURE_TAG_DEFS = {
    "entry_not_healthy": {
        "desc": "入场时 verdict 不含'健康'",
        "action": "提高入场质量门槛",
    },
    "entry_15m_hot": {
        "desc": "入场时 15m 涨幅过高",
        "action": "降低 MAX_ENTRY_CHANGE_15M",
    },
    "entry_1h_hot": {
        "desc": "入场时 1h 涨幅过高",
        "action": "降低 MAX_ENTRY_CHANGE_1H",
    },
    "entry_funding_hot": {
        "desc": "入场时资金费率过高",
        "action": "降低 MAX_ENTRY_FUNDING_PCT",
    },
    "entry_lsr_hot": {
        "desc": "入场时散户 LSR 过高",
        "action": "降低 MAX_ENTRY_LSR",
    },
    "oi15_reversed": {
        "desc": "退出时 OI 15m 变化为负",
        "action": "要求入场时 OI 15m 增加",
    },
    "oi1h_reversed": {
        "desc": "退出时 OI 1h 变化为负",
        "action": "要求入场时 OI 1h 增加",
    },
    "oi4h_reversed": {
        "desc": "退出时 OI 4h 变化为负",
        "action": "要求入场时 OI 4h 增加",
    },
    "buy_pressure_faded": {
        "desc": "退出时主动买入压力减弱",
        "action": "要求入场时 taker 趋势更强",
    },
    "tp1_hit_then_reversal": {
        "desc": "TP1 触发后价格反转",
        "action": "TP1 后更快移至保本",
    },
    "sl_too_tight": {
        "desc": "止损过小，被正常波动触发",
        "action": "增加 ATR_STOP_MULTIPLIER",
    },
    "sl_too_wide": {
        "desc": "止损过大，亏损超预期",
        "action": "减小 ATR_STOP_MULTIPLIER",
    },
    "heat_declined": {
        "desc": "社交热度在持仓期间下降",
        "action": "要求入场时热度稳定或上升",
    },
    "price_hit_stop": {
        "desc": "正常止损触发（兜底标签）",
        "action": "无需调整",
    },
}

# === Thresholds for rule suggestions ===
TAG_FREQUENCY_THRESHOLD = 3  # Tag must appear this many times to suggest a rule change
WEIGHT_LOOKBACK_TRADES = 20  # Look at this many recent trades for strategy weights
WEIGHT_MINIMUM = 0.05        # Minimum weight any strategy can have
WEIGHT_DECAY_FACTOR = 0.9    # Exponential decay for older trades in weight calculation


class FailureArchive:
    """Archive failed trades with root-cause tags."""

    @staticmethod
    def analyze_failure(
        trade: dict,
        entry_snapshot: dict | None = None,
        exit_snapshot: dict | None = None,
    ) -> list[str]:
        """
        Analyze a stopped-out trade and return failure tags.

        Args:
            trade: Trade record with pre_analysis, entry_price, direction, etc.
            entry_snapshot: Market snapshot at entry time (from pre_analysis)
            exit_snapshot: Current market snapshot at exit time

        Returns:
            List of failure tag strings
        """
        tags = []
        pre = trade.get("pre_analysis", {})
        if isinstance(pre, str):
            try:
                pre = json.loads(pre)
            except Exception:
                pre = {}

        # --- Entry condition tags ---
        verdict = pre.get("verdict", "") or ""
        if "健康" not in verdict:
            tags.append("entry_not_healthy")

        entry_snapshot = entry_snapshot or pre.get("snapshot", {})
        if entry_snapshot:
            change_15m = entry_snapshot.get("change_15m", 0) or 0
            change_1h = entry_snapshot.get("change_1h", 0) or 0
            funding = entry_snapshot.get("funding_rate", 0) or 0
            global_lsr = entry_snapshot.get("global_lsr", 1.0) or 1.0

            # 15m/1h overheated
            if change_15m > 2.0:
                tags.append("entry_15m_hot")
            if change_1h > 5.0:
                tags.append("entry_1h_hot")

            # Funding too hot
            if abs(funding) >= 0.05:
                tags.append("entry_funding_hot")

            # Retail LSR too high
            if global_lsr >= 1.7:
                tags.append("entry_lsr_hot")

        # --- Exit condition tags ---
        if exit_snapshot:
            oi_15m = exit_snapshot.get("oi_15m_change", 0) or 0
            oi_1h = exit_snapshot.get("oi_1h_change", 0) or 0
            oi_4h = exit_snapshot.get("oi_4h_change", 0) or 0
            taker_ratio = exit_snapshot.get("taker_ratio", 1.0) or 1.0

            if oi_15m <= 0:
                tags.append("oi15_reversed")
            if oi_1h <= 0:
                tags.append("oi1h_reversed")
            if oi_4h <= 0:
                tags.append("oi4h_reversed")
            if taker_ratio < 0.8:
                tags.append("buy_pressure_faded")

        # --- TP1 reversal check ---
        if trade.get("tp1_done") and trade.get("status") == "closed":
            pnl = trade.get("pnl_pct", 0) or 0
            if pnl < -1.0:
                tags.append("tp1_hit_then_reversal")

        # --- SL sizing check ---
        atr_pct = entry_snapshot.get("atr_pct", 0) if entry_snapshot else 0
        if atr_pct > 0:
            sl_distance = abs((trade.get("entry_price", 0) or 0) - (trade.get("stop_loss", 0) or 0)) / (trade.get("entry_price", 1) or 1)
            expected_sl = atr_pct * 1.5 / 100  # ATR * 1.5
            if sl_distance < expected_sl * 0.5:
                tags.append("sl_too_tight")
            elif sl_distance > expected_sl * 2.0:
                tags.append("sl_too_wide")

        # --- Heat decline check (if social heat was available) ---
        heat_entry = pre.get("heat_score", 0) if pre else 0
        if heat_entry and exit_snapshot:
            heat_exit = exit_snapshot.get("heat_score", 0) or 0
            if heat_exit < heat_entry * 0.5:
                tags.append("heat_declined")

        # Fallback tag
        if not tags:
            tags.append("price_hit_stop")

        return tags

    @staticmethod
    def archive(trade_id: int, tags: list[str] | None = None, exit_reason: str = "止损") -> dict:
        """
        Archive a failed trade with tags.

        Args:
            trade_id: Trade ID
            tags: List of failure tags (auto-analyzed if None)
            exit_reason: Exit reason string

        Returns:
            Archive record dict
        """
        init_db()
        conn = get_db()
        c = conn.cursor()

        # Get trade details
        c.execute("SELECT * FROM trades WHERE id=?", (trade_id,))
        trade = c.fetchone()
        if not trade:
            return {"ok": False, "error": "trade_not_found"}

        trade_dict = dict(trade)
        pre = trade_dict.get("pre_analysis", {})
        if isinstance(pre, str):
            try:
                pre = json.loads(pre)
            except Exception:
                pre = {}

        # Get current snapshot for exit analysis
        exit_snapshot = {}
        try:
            from .market_snapshot import get_market_snapshot
            exit_snapshot = get_market_snapshot(trade_dict["symbol"])
        except Exception:
            pass

        entry_snapshot = pre.get("snapshot", {})

        # Analyze if tags not provided
        if not tags:
            tags = FailureArchive.analyze_failure(trade_dict, entry_snapshot, exit_snapshot)

        # Insert into failure_archive
        c.execute(
            """
            INSERT INTO failure_archive
            (trade_id, symbol, signal_type, direction, entry_price, exit_price,
             pnl_pct, exit_reason, tags, entry_snapshot_json, exit_snapshot_json,
             archived_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_id,
                trade_dict["symbol"],
                pre.get("type", ""),
                trade_dict["direction"],
                trade_dict["entry_price"],
                trade_dict.get("exit_price"),
                trade_dict.get("pnl_pct", 0),
                exit_reason,
                json.dumps(tags, ensure_ascii=False),
                json.dumps(entry_snapshot, ensure_ascii=False),
                json.dumps(exit_snapshot, ensure_ascii=False),
                int(time.time()),
            ),
        )
        conn.commit()

        return {
            "ok": True,
            "trade_id": trade_id,
            "tags": tags,
            "archived_at": int(time.time()),
        }

    @staticmethod
    def get_tag_stats(window_trades: int = 50) -> list[dict]:
        """
        Get failure tag frequency statistics.

        Args:
            window_trades: Number of recent archived failures to analyze

        Returns:
            List of {tag, count, frequency, desc, action}
        """
        init_db()
        conn = get_db()
        c = conn.cursor()

        c.execute(
            """
            SELECT tags FROM failure_archive
            ORDER BY id DESC LIMIT ?
            """,
            (window_trades,),
        )

        tag_counts: dict[str, int] = {}
        total = 0
        for row in c.fetchall():
            tags = json.loads(row["tags"]) if row["tags"] else []
            total += len(tags)
            for t in tags:
                tag_counts[t] = tag_counts.get(t, 0) + 1

        stats = []
        for tag, count in sorted(tag_counts.items(), key=lambda x: -x[1]):
            defn = FAILURE_TAG_DEFS.get(tag, {})
            stats.append({
                "tag": tag,
                "count": count,
                "frequency": round(count / total * 100, 1) if total > 0 else 0,
                "desc": defn.get("desc", ""),
                "action": defn.get("action", ""),
            })

        return stats

    @staticmethod
    def get_recent_failures(limit: int = 20) -> list[dict]:
        """Get recent archived failures."""
        init_db()
        conn = get_db()
        c = conn.cursor()
        c.execute(
            """
            SELECT * FROM failure_archive
            ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        )
        results = []
        for row in c.fetchall():
            item = dict(row)
            item["tags"] = json.loads(item["tags"]) if item["tags"] else []
            item["entry_snapshot"] = json.loads(item["entry_snapshot_json"]) if item["entry_snapshot_json"] else {}
            item["exit_snapshot"] = json.loads(item["exit_snapshot_json"]) if item["exit_snapshot_json"] else {}
            item.pop("entry_snapshot_json", None)
            item.pop("exit_snapshot_json", None)
            results.append(item)
        return results


class StrategyWeighter:
    """Compute dynamic strategy weights based on recent performance."""

    @staticmethod
    def get_weights(
        lookback: int = WEIGHT_LOOKBACK_TRADES,
        min_weight: float = WEIGHT_MINIMUM,
        decay: float = WEIGHT_DECAY_FACTOR,
    ) -> dict[str, float]:
        """
        Get adaptive weights for each strategy.

        Higher weight = strategy is performing well recently.
        Weights are normalized to sum to 1.0.

        Args:
            lookback: Number of recent closed trades to consider
            min_weight: Minimum weight any strategy gets
            decay: Exponential decay factor for older trades

        Returns:
            Dict of {strategy_name: weight} normalized to sum 1.0
        """
        init_db()
        conn = get_db()
        c = conn.cursor()

        # Get recent closed trades with strategy type
        c.execute(
            """
            SELECT
                json_extract(pre_analysis, '$.type') as signal_type,
                pnl_pct,
                id
            FROM trades
            WHERE status='closed'
            AND json_extract(pre_analysis, '$.type') IS NOT NULL
            ORDER BY id DESC LIMIT ?
            """,
            (lookback,),
        )

        trades = [dict(row) for row in c.fetchall()]
        if not trades:
            # No data yet — equal weights
            strategies = list(STRATEGY_CONFIGS.keys())
            base = 1.0 / len(strategies)
            return {s: round(base, 4) for s in strategies}

        # Compute per-strategy score using decay-weighted performance
        raw_scores: dict[str, float] = {}
        total_trades: dict[str, int] = {}

        for i, t in enumerate(trades):
            sig = t["signal_type"]
            if not sig:
                continue

            # Decay weight: most recent = 1.0, oldest = decay^lookback
            w = decay ** (lookback - 1 - i)
            pnl = t["pnl_pct"] or 0

            # Score: +1 for win, -0.5 for loss (risk-adjusted)
            trade_score = w * (1.0 if pnl > 0 else -0.5)

            raw_scores[sig] = raw_scores.get(sig, 0) + trade_score
            total_trades[sig] = total_trades.get(sig, 0) + 1

        # Convert raw scores to weights
        all_strategies = set(STRATEGY_CONFIGS.keys()) | set(raw_scores.keys())
        weights: dict[str, float] = {}

        for s in all_strategies:
            score = raw_scores.get(s, 0)
            count = total_trades.get(s, 0)

            # Base weight from score (shift to positive range)
            # Score range: -0.5*count to +1.0*count
            # Normalize to 0-1 range
            if count > 0:
                normalized = (score / count + 0.5) / 1.5  # Maps to ~[0, 1]
            else:
                normalized = 0.5  # Neutral for unseen strategies

            # Apply minimum weight
            weights[s] = max(normalized, min_weight)

        # Normalize to sum to 1.0
        total = sum(weights.values())
        if total > 0:
            weights = {s: round(w / total, 4) for s, w in weights.items()}
        else:
            base = 1.0 / len(weights)
            weights = {s: round(base, 4) for s in weights}

        return weights

    @staticmethod
    def get_strategy_priority(
        lookback: int = WEIGHT_LOOKBACK_TRADES,
    ) -> list[dict]:
        """
        Get strategy ranking by recent performance.

        Returns:
            List of {strategy, weight, win_rate, avg_pnl, trade_count} sorted by weight desc
        """
        init_db()
        conn = get_db()
        c = conn.cursor()

        weights = StrategyWeighter.get_weights(lookback=lookback)

        # Get stats per strategy
        c.execute(
            """
            SELECT
                json_extract(pre_analysis, '$.type') as signal_type,
                COUNT(*) as total,
                SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
                AVG(pnl_pct) as avg_pnl,
                SUM(pnl_usd) as total_pnl
            FROM trades
            WHERE status='closed'
            AND json_extract(pre_analysis, '$.type') IS NOT NULL
            GROUP BY signal_type
            """,
        )

        stats = {}
        for row in c.fetchall():
            sig = row["signal_type"]
            total = row["total"] or 0
            wins = row["wins"] or 0
            stats[sig] = {
                "trade_count": total,
                "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
                "avg_pnl": round(row["avg_pnl"] or 0, 2),
                "total_pnl": round(row["total_pnl"] or 0, 4),
            }

        results = []
        for s, w in sorted(weights.items(), key=lambda x: -x[1]):
            s_stats = stats.get(s, {"trade_count": 0, "win_rate": 0, "avg_pnl": 0, "total_pnl": 0})
            results.append({
                "strategy": s,
                "weight": w,
                **s_stats,
            })

        return results


class RuleReflector:
    """Suggest rule changes based on failure tag patterns."""

    @staticmethod
    def get_suggestions(
        min_frequency: int = TAG_FREQUENCY_THRESHOLD,
        window_trades: int = 50,
    ) -> list[dict]:
        """
        Generate rule change suggestions from frequent failure tags.

        Args:
            min_frequency: Minimum tag count to trigger suggestion
            window_trades: Number of recent failures to analyze

        Returns:
            List of {tag, count, current_rule, suggested_action, confidence}
        """
        tag_stats = FailureArchive.get_tag_stats(window_trades=window_trades)
        suggestions = []

        for stat in tag_stats:
            if stat["count"] < min_frequency:
                continue

            tag = stat["tag"]
            defn = FAILURE_TAG_DEFS.get(tag, {})
            confidence = min(stat["frequency"] / 20.0, 1.0)  # Higher frequency = higher confidence

            # Map tag to current rule and suggested action
            rule_mapping = {
                "entry_not_healthy": {
                    "current_rule": "允许非健康 verdict 入场",
                    "suggested_action": "添加硬门槛: 仅允许 '健康' 或 '值得留意' verdict 入场",
                    "param": "MIN_VERDICT_FOR_ENTRY",
                },
                "entry_15m_hot": {
                    "current_rule": "TRADING_MAX_ENTRY_CHANGE_15M = 2%",
                    "suggested_action": "降低至 1.0% 或要求 15m 回落",
                    "param": "MAX_ENTRY_CHANGE_15M",
                },
                "entry_1h_hot": {
                    "current_rule": "1h 涨幅无硬限制",
                    "suggested_action": "添加 MAX_ENTRY_CHANGE_1H = 5%",
                    "param": "MAX_ENTRY_CHANGE_1H",
                },
                "entry_funding_hot": {
                    "current_rule": "资金费率无入场限制",
                    "suggested_action": "添加 funding >= 0.05% 硬否决",
                    "param": "MAX_ENTRY_FUNDING_PCT",
                },
                "entry_lsr_hot": {
                    "current_rule": "LSR 无入场限制",
                    "suggested_action": "添加 LSR >= 1.7 硬否决",
                    "param": "MAX_ENTRY_LSR",
                },
                "oi15_reversed": {
                    "current_rule": "OI 15m 仅作为评分项",
                    "suggested_action": "要求 OI 15m > 0 作为入场条件",
                    "param": "REQUIRE_OI_15M_UP",
                },
                "oi1h_reversed": {
                    "current_rule": "OI 1h 仅作为评分项",
                    "suggested_action": "要求 OI 1h > 0 作为入场条件",
                    "param": "REQUIRE_OI_1H_UP",
                },
                "buy_pressure_faded": {
                    "current_rule": "taker 趋势仅作为评分项",
                    "suggested_action": "要求 taker_trend_pct > -2%",
                    "param": "MIN_TAKER_TREND",
                },
                "sl_too_tight": {
                    "current_rule": "ATR_STOP_MULTIPLIER = 1.5",
                    "suggested_action": "增加至 2.0",
                    "param": "ATR_STOP_MULTIPLIER",
                },
                "sl_too_wide": {
                    "current_rule": "ATR_STOP_MULTIPLIER = 1.5",
                    "suggested_action": "减小至 1.0",
                    "param": "ATR_STOP_MULTIPLIER",
                },
            }

            rule_info = rule_mapping.get(tag, {})
            if rule_info:
                suggestions.append({
                    "tag": tag,
                    "count": stat["count"],
                    "frequency": stat["frequency"],
                    "desc": defn.get("desc", ""),
                    "current_rule": rule_info.get("current_rule", ""),
                    "suggested_action": rule_info.get("suggested_action", stat["action"]),
                    "param": rule_info.get("param", ""),
                    "confidence": round(confidence, 2),
                })

        # Sort by count descending
        suggestions.sort(key=lambda x: -x["count"])
        return suggestions

    @staticmethod
    def apply_suggestion(suggestion: dict) -> bool:
        """
        Apply a rule suggestion to the config.
        This is intentionally conservative — only writes to state.json,
        does not mutate running config.

        Args:
            suggestion: Dict from get_suggestions()

        Returns:
            True if applied successfully
        """
        from pathlib import Path
        state_path = Path.home() / ".hermes" / "trading_core" / "state.json"

        try:
            if state_path.exists():
                with open(state_path) as f:
                    state = json.load(f)
            else:
                state = {}

            rule_changes = state.get("rule_changes", {})
            rule_changes[suggestion["param"]] = {
                "tag": suggestion["tag"],
                "action": suggestion["suggested_action"],
                "applied_at": int(time.time()),
                "count": suggestion["count"],
                "confidence": suggestion["confidence"],
            }
            state["rule_changes"] = rule_changes

            with open(state_path, "w") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)

            return True
        except Exception:
            return False
