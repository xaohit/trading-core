"""
Daily trader reflection report.

Summarizes the system's activity so Hermes can act like a human trading
reviewer without reading the whole database every time.
"""
from __future__ import annotations

from collections import Counter

try:
    from .db.trades import TradeDB
    from .decision_memory import DecisionMemory
    from .semantic_radar import SemanticRadar
except ImportError:
    from db.trades import TradeDB
    from decision_memory import DecisionMemory
    from semantic_radar import SemanticRadar


def build_daily_reflection_report(limit: int = 80) -> dict:
    signals = TradeDB.get_recent_signals(limit)
    decisions = DecisionMemory.recent_decisions(40)
    experiences = DecisionMemory.recent_experiences(20)

    action_counts = Counter(row.get("action") or "unknown" for row in signals)
    signal_counts = Counter(row.get("signal_type") or "unknown" for row in signals)
    decision_actions = Counter(row.get("action") or "unknown" for row in decisions)

    rejects = [
        row
        for row in signals
        if row.get("action") and row.get("action") != "opened"
    ]
    opened = [row for row in signals if row.get("action") == "opened"]

    return {
        "summary": {
            "signals_seen": len(signals),
            "opened": len(opened),
            "rejected": len(rejects),
            "recent_decisions": len(decisions),
            "recent_experiences": len(experiences),
        },
        "action_counts": dict(action_counts),
        "signal_counts": dict(signal_counts),
        "decision_actions": dict(decision_actions),
        "top_reject_reasons": _top_reject_reasons(rejects),
        "semantic_events": SemanticRadar.recent(20),
        "review_questions": [
            "Which rejection layer is dominating, and is it too strict?",
            "Which signal families repeatedly reach Agent review?",
            "Which experiences are being reused, and are they still valid?",
            "Did any semantic/macro event invalidate a local-only decision?",
            "What one threshold or rule should be watched tomorrow?",
        ],
    }


def _top_reject_reasons(rows: list[dict]) -> list[dict]:
    reasons = Counter()
    for row in rows:
        action = row.get("action") or "unknown"
        result = row.get("result") or ""
        reasons[f"{action}: {result[:120]}"] += 1
    return [
        {"reason": reason, "count": count}
        for reason, count in reasons.most_common(10)
    ]
