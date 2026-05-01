"""
Decision providers.

The scanner asks a provider for the final trade/wait judgment. The default
provider is local and deterministic; Hermes can be wired in later without
changing scanner, risk, execution, or memory code.
"""
from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Any

try:
    from .agent_decision import AgentDecisionGate
    from .trade_hypothesis import build_hypothesis
except ImportError:
    from agent_decision import AgentDecisionGate
    from trade_hypothesis import build_hypothesis


class DecisionProvider(ABC):
    name = "base"

    @abstractmethod
    def decide(
        self,
        symbol: str,
        signal: dict,
        snapshot: dict,
        analysis: dict,
        experiences: list[dict] | None = None,
        semantic_events: list[dict] | None = None,
    ) -> dict:
        raise NotImplementedError


class LocalDecisionProvider(DecisionProvider):
    name = "local"

    def decide(
        self,
        symbol: str,
        signal: dict,
        snapshot: dict,
        analysis: dict,
        experiences: list[dict] | None = None,
        semantic_events: list[dict] | None = None,
    ) -> dict:
        experiences = experiences or []
        semantic_events = semantic_events or []
        decision = AgentDecisionGate.evaluate(symbol, signal, snapshot, analysis, experiences)
        decision["provider"] = self.name
        decision["semantic_events"] = semantic_events
        decision["hypothesis"] = build_hypothesis(
            signal=signal,
            analysis=analysis,
            experiences=experiences,
            reasoning=decision.get("reasoning", ""),
        ).to_dict()
        return decision


class HermesDecisionProvider(DecisionProvider):
    """
    Placeholder provider contract for Hermes.

    Set HERMES_DECISION_PROVIDER=hermes only after an actual client is wired.
    Until then this provider returns a safe wait decision.
    """

    name = "hermes"

    def decide(
        self,
        symbol: str,
        signal: dict,
        snapshot: dict,
        analysis: dict,
        experiences: list[dict] | None = None,
        semantic_events: list[dict] | None = None,
    ) -> dict:
        return {
            "approved": False,
            "action": "wait",
            "requested_action": _requested_action(signal.get("direction")),
            "conviction": 0,
            "provider": self.name,
            "reasoning": "Hermes provider is selected but no Hermes client is wired; safe wait.",
            "experience_count": len(experiences or []),
            "semantic_events": semantic_events or [],
            "hypothesis": build_hypothesis(signal, analysis, experiences or []).to_dict(),
        }


class EventTriggeredDecisionProvider(DecisionProvider):
    """
    Provider router.

    Local handles ordinary opportunities. Hermes should only be triggered for
    ambiguous/high-value situations or semantic events, keeping token usage low.
    """

    name = "event_router"

    def __init__(self, local: DecisionProvider | None = None, hermes: DecisionProvider | None = None):
        self.local = local or LocalDecisionProvider()
        self.hermes = hermes or HermesDecisionProvider()

    def decide(
        self,
        symbol: str,
        signal: dict,
        snapshot: dict,
        analysis: dict,
        experiences: list[dict] | None = None,
        semantic_events: list[dict] | None = None,
    ) -> dict:
        semantic_events = semantic_events or []
        if should_trigger_hermes(signal, analysis, experiences or [], semantic_events):
            decision = self.hermes.decide(symbol, signal, snapshot, analysis, experiences, semantic_events)
            decision["router_reason"] = "hermes_triggered"
            return decision
        decision = self.local.decide(symbol, signal, snapshot, analysis, experiences, semantic_events)
        decision["router_reason"] = "local_sufficient"
        return decision


def get_decision_provider() -> DecisionProvider:
    mode = os.getenv("DECISION_PROVIDER", "event").strip().lower()
    if mode == "local":
        return LocalDecisionProvider()
    if mode == "hermes":
        return HermesDecisionProvider()
    return EventTriggeredDecisionProvider()


def should_trigger_hermes(
    signal: dict,
    analysis: dict,
    experiences: list[dict],
    semantic_events: list[dict],
) -> bool:
    if any((event.get("severity") or 0) >= 70 for event in semantic_events):
        return True

    strength = signal.get("strength")
    score = _num(signal.get("composite_score"), _num(analysis.get("score"), 0))
    if strength == "S" and score >= 68:
        return True

    wins = sum(1 for e in experiences if e.get("outcome_label") in {"target_hit", "direction_correct"})
    losses = sum(1 for e in experiences if e.get("outcome_label") in {"invalidated", "direction_wrong"})
    if wins and losses:
        return True

    return 60 <= score <= 85 and strength == "A" and len(experiences) >= 2


def parse_provider_json(raw: str) -> dict:
    """Parse a Hermes JSON decision response with a small safety net."""
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("provider response must be a JSON object")
    action = data.get("action", "wait")
    if action not in {"open_long", "open_short", "wait", "close"}:
        data["action"] = "wait"
        data["approved"] = False
    return data


def _requested_action(direction: str | None) -> str:
    if direction == "long":
        return "open_long"
    if direction == "short":
        return "open_short"
    return "wait"


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
