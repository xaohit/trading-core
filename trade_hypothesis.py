"""
Structured trade hypothesis model.

Every Agent decision should explain the trade as a falsifiable hypothesis:
why it might work, how it should unfold, and what proves it wrong.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class TradeHypothesis:
    hypothesis: str
    expected_path: str
    invalidation: str
    ignored_risks: list[str] = field(default_factory=list)
    experience_refs: list[int] = field(default_factory=list)
    time_horizon: str = "24h"

    def to_dict(self) -> dict:
        return asdict(self)


def build_hypothesis(
    signal: dict,
    analysis: dict,
    experiences: list[dict] | None = None,
    reasoning: str = "",
) -> TradeHypothesis:
    experiences = experiences or []
    direction = signal.get("direction") or "unknown"
    signal_type = signal.get("type") or "unknown"
    verdict = analysis.get("verdict") or "unknown"
    tags = analysis.get("tags") or []
    refs = [
        int(exp["id"])
        for exp in experiences[:5]
        if _is_int_like(exp.get("id"))
    ]

    return TradeHypothesis(
        hypothesis=(
            f"{signal_type} suggests a {direction} setup if current market "
            f"context remains consistent with {verdict}."
        ),
        expected_path=(
            "Price should move favorably within the review horizon without "
            "triggering the invalidation level first."
        ),
        invalidation=(
            "Invalidate if price reaches stop/risk level, market tags flip to "
            "crowded/overheated against the direction, or similar failed "
            "experiences dominate the context."
        ),
        ignored_risks=_risk_notes(tags, reasoning),
        experience_refs=refs,
    )


def _risk_notes(tags: list[str], reasoning: str) -> list[str]:
    risks = []
    tag_set = set(tags or [])
    if tag_set & {"funding_hot", "long_crowded", "short_crowded"}:
        risks.append("crowding risk")
    if tag_set & {"buy_pressure_falling", "taker_weak"}:
        risks.append("weak flow risk")
    if tag_set & {"price_up_oi_down", "price_down_oi_up"}:
        risks.append("price/OI divergence risk")
    if "no_match" in reasoning:
        risks.append("limited historical experience")
    return risks or ["unknown macro/semantic risk"]


def _is_int_like(value: Any) -> bool:
    try:
        int(value)
        return True
    except (TypeError, ValueError):
        return False
