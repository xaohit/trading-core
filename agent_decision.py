"""
Agent decision gate.

Rules find candidates; this layer decides whether the candidate deserves a
trade, using current context plus retrieved experience. It is deliberately
local and deterministic until Hermes is wired as the decision provider.
"""
from __future__ import annotations

from typing import Any


class AgentDecisionGate:
    """Final Agent-style approval layer before local risk/execution."""

    MIN_APPROVAL_CONVICTION = 58.0

    @staticmethod
    def evaluate(
        symbol: str,
        signal: dict,
        snapshot: dict,
        analysis: dict,
        experiences: list[dict] | None = None,
    ) -> dict:
        experiences = experiences or []
        score = _num(signal.get("composite_score"), _num(analysis.get("score"), 50.0))
        strength = signal.get("strength", "B")
        direction = signal.get("direction")
        signal_type = signal.get("type", "unknown")

        conviction = float(score)
        reasons = [
            f"base_score={score:.1f}",
            f"signal={signal_type}/{strength}/{direction}",
        ]

        strength_delta = {"S": 8.0, "A": 3.0, "B": -10.0}.get(strength, -6.0)
        conviction += strength_delta
        reasons.append(f"strength_delta={strength_delta:+.1f}")

        exp_delta, exp_notes = AgentDecisionGate._experience_delta(experiences)
        conviction += exp_delta
        if exp_notes:
            reasons.extend(exp_notes)

        tags = set(analysis.get("tags") or [])
        if tags & {"price_overheated", "funding_hot", "long_crowded", "taker_overheated"}:
            conviction -= 12.0
            reasons.append("crowding_or_overheat_discount=-12.0")

        conviction = round(max(0.0, min(100.0, conviction)), 1)
        requested_action = "open_long" if direction == "long" else "open_short" if direction == "short" else "wait"

        if conviction < AgentDecisionGate.MIN_APPROVAL_CONVICTION:
            return {
                "approved": False,
                "action": "wait",
                "requested_action": requested_action,
                "conviction": conviction,
                "reasoning": " | ".join(reasons + [f"below_min={AgentDecisionGate.MIN_APPROVAL_CONVICTION}"]),
                "experience_count": len(experiences),
            }

        return {
            "approved": True,
            "action": requested_action,
            "requested_action": requested_action,
            "conviction": conviction,
            "reasoning": " | ".join(reasons),
            "experience_count": len(experiences),
        }

    @staticmethod
    def _experience_delta(experiences: list[dict]) -> tuple[float, list[str]]:
        if not experiences:
            return 0.0, ["experience_delta=+0.0/no_match"]

        delta = 0.0
        wins = 0
        losses = 0
        notes: list[str] = []

        for exp in experiences[:5]:
            outcome = exp.get("outcome_label")
            adjustment = exp.get("adjustment") or {}
            if outcome in {"target_hit", "direction_correct"}:
                wins += 1
                delta += 4.0
            elif outcome in {"invalidated", "direction_wrong"}:
                losses += 1
                delta -= 7.0

            adj_delta = _num(adjustment.get("conviction_delta"), 0.0)
            delta += adj_delta
            if adjustment.get("requires_extra_confirmation"):
                delta -= 2.0

        delta = max(-18.0, min(12.0, delta))
        notes.append(f"experience_delta={delta:+.1f}/wins={wins}/losses={losses}")
        return delta, notes


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
