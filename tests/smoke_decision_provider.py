"""
Smoke tests for decision provider architecture.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decision_provider import EventTriggeredDecisionProvider, LocalDecisionProvider, should_trigger_hermes
from semantic_radar import SemanticRadar
from trade_hypothesis import build_hypothesis


def _signal(score=72, strength="A"):
    return {
        "symbol": "SOLUSDT",
        "type": "neg_funding_long",
        "direction": "long",
        "strength": strength,
        "composite_score": score,
    }


def _analysis(score=72):
    return {"score": score, "verdict": "healthy", "tags": ["liquid", "oi_high"]}


def test_local_provider_outputs_hypothesis():
    decision = LocalDecisionProvider().decide(
        "SOLUSDT",
        _signal(),
        {"price": 100},
        _analysis(),
        [{"id": 1, "outcome_label": "target_hit"}],
    )
    assert decision["provider"] == "local"
    assert "hypothesis" in decision
    assert decision["hypothesis"]["experience_refs"] == [1]

    print("  [OK] Local provider outputs hypothesis")


def test_event_router_triggers_hermes_safely():
    event = {"symbol": "SOLUSDT", "severity": 90, "event_type": "news"}
    assert should_trigger_hermes(_signal(), _analysis(), [], [event]) is True

    decision = EventTriggeredDecisionProvider().decide(
        "SOLUSDT",
        _signal(),
        {"price": 100},
        _analysis(),
        [],
        [event],
    )
    assert decision["provider"] == "hermes"
    assert decision["approved"] is False
    assert decision["action"] == "wait"

    print("  [OK] Event router triggers Hermes safely")


def test_semantic_radar_and_hypothesis():
    SemanticRadar._events.clear()
    SemanticRadar.add_event("SOLUSDT", "macro", 75, "bearish", "Macro risk rising")
    events = SemanticRadar.events_for("SOLUSDT")
    assert len(events) == 1
    assert events[0]["severity"] == 75

    hypothesis = build_hypothesis(_signal(), _analysis(), [{"id": 2}], "no_match")
    assert "limited historical experience" in hypothesis.ignored_risks

    print("  [OK] Semantic radar and hypothesis model correct")


if __name__ == "__main__":
    print("Decision Provider Smoke Tests")
    print("=" * 40)

    test_local_provider_outputs_hypothesis()
    test_event_router_triggers_hermes_safely()
    test_semantic_radar_and_hypothesis()

    print("=" * 40)
    print("DECISION_PROVIDER_SMOKE_OK")
