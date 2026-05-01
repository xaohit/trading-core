"""
Smoke tests for the Agent decision gate.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_decision import AgentDecisionGate
from scanner import Scanner


def _signal(score=68, strength="A"):
    return {
        "symbol": "SOLUSDT",
        "type": "neg_funding_long",
        "direction": "long",
        "strength": strength,
        "price": 100.0,
        "sl_pct": 0.05,
        "composite_score": score,
    }


def test_agent_approves_good_setup():
    decision = AgentDecisionGate.evaluate(
        "SOLUSDT",
        _signal(),
        {"price": 100.0},
        {"score": 68, "tags": ["liquid", "oi_high"]},
        [{"outcome_label": "target_hit", "adjustment": {"conviction_delta": 2}}],
    )
    assert decision["approved"] is True
    assert decision["action"] == "open_long"
    assert decision["conviction"] >= 58

    print("  [OK] Agent approves good setup")


def test_agent_rejects_bad_memory():
    decision = AgentDecisionGate.evaluate(
        "SOLUSDT",
        _signal(score=58),
        {"price": 100.0},
        {"score": 58, "tags": ["liquid"]},
        [
            {"outcome_label": "direction_wrong", "adjustment": {"conviction_delta": -8}},
            {"outcome_label": "invalidated", "adjustment": {"requires_extra_confirmation": True}},
        ],
    )
    assert decision["approved"] is False
    assert decision["action"] == "wait"
    assert "experience_delta" in decision["reasoning"]

    print("  [OK] Agent rejects bad memory")


def test_agent_stop_loss_planning():
    long_sl = Scanner._planned_stop_loss({"direction": "long", "sl_pct": 0.05}, 100.0)
    short_sl = Scanner._planned_stop_loss({"direction": "short", "sl_pct": 0.05}, 100.0)
    assert long_sl == 95.0
    assert short_sl == 105.0

    print("  [OK] Agent stop loss planning correct")


if __name__ == "__main__":
    print("Agent Gate Smoke Tests")
    print("=" * 40)

    test_agent_approves_good_setup()
    test_agent_rejects_bad_memory()
    test_agent_stop_loss_planning()

    print("=" * 40)
    print("AGENT_GATE_SMOKE_OK")
