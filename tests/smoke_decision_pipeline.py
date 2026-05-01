"""
Smoke tests for the pre-Agent decision pipeline.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decision_pipeline import DecisionPipeline


class FakeRisk:
    def __init__(self, allowed=True, quality="FULL", passed=6):
        self.allowed = allowed
        self.quality = quality
        self.passed = passed

    def evaluate_entry_quality(self, symbol, signal, analysis):
        return self.quality, self.passed, ["fake_quality"]

    def check_account_risk(self, symbol):
        return self.allowed, "fake_risk_reject"


def _signal():
    return {"type": "neg_funding_long", "direction": "long", "strength": "A"}


def _analysis(score=70, tags=None, verdict="healthy"):
    return {"score": score, "tags": tags or ["liquid"], "verdict": verdict}


def _snapshot():
    return {
        "change_4h": 3,
        "change_24h": 8,
        "funding_rate": 0.01,
        "global_lsr": 1.1,
        "taker_ratio": 1.1,
        "taker_trend_pct": 1,
    }


def test_pipeline_accepts_clean_candidate():
    signal = _signal()
    decision = DecisionPipeline(FakeRisk()).evaluate(
        "SOLUSDT", signal, _snapshot(), _analysis(), True, {"verdict": "ok"}, 4
    )
    assert decision.ok is True
    assert decision.action == "candidate_ok"
    assert signal["entry_quality"] == "FULL"

    print("  [OK] Pipeline accepts clean candidate")


def test_pipeline_rejects_score_tags():
    decision = DecisionPipeline(FakeRisk()).evaluate(
        "SOLUSDT", _signal(), _snapshot(), _analysis(tags=["funding_hot"]), True, {}, 4
    )
    assert decision.ok is False
    assert decision.action == "score_reject"

    print("  [OK] Pipeline rejects hard score tags")


def test_pipeline_rejects_quality_and_risk():
    quality_decision = DecisionPipeline(FakeRisk(quality="SKIP", passed=1)).evaluate(
        "SOLUSDT", _signal(), _snapshot(), _analysis(), True, {}, 4
    )
    assert quality_decision.ok is False
    assert quality_decision.action == "quality_reject"

    risk_decision = DecisionPipeline(FakeRisk(allowed=False)).evaluate(
        "SOLUSDT", _signal(), _snapshot(), _analysis(), True, {}, 4
    )
    assert risk_decision.ok is False
    assert risk_decision.action == "risk_reject"

    print("  [OK] Pipeline rejects quality and risk")


if __name__ == "__main__":
    print("Decision Pipeline Smoke Tests")
    print("=" * 40)

    test_pipeline_accepts_clean_candidate()
    test_pipeline_rejects_score_tags()
    test_pipeline_rejects_quality_and_risk()

    print("=" * 40)
    print("DECISION_PIPELINE_SMOKE_OK")
