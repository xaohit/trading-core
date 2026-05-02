"""
Decision pipeline.

Keeps candidate filtering in one place so the scanner can stay focused on
orchestration: discover signals, rank candidates, execute approved trades.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

try:
    from .config import ENTRY_QUALITY_MIN_PASSED, ENTRY_QUALITY_MIN_SCORE, STATE_PATH
except ImportError:
    from config import ENTRY_QUALITY_MIN_PASSED, ENTRY_QUALITY_MIN_SCORE, STATE_PATH


DEFAULT_VETO_THRESHOLDS = {
    "change_4h_pct": 25.0,
    "change_24h_pct": 50.0,
    "funding_pct": 0.05,
    "lsr_pct": 1.7,
    "taker_ratio": 1.8,
    "long_taker_trend_pct": -5.0,
    "short_taker_trend_pct": 5.0,
}


@dataclass
class PipelineDecision:
    ok: bool
    action: str
    reason: str
    score: float
    details: dict = field(default_factory=dict)


class DecisionPipeline:
    """Pre-Agent candidate quality pipeline."""

    def __init__(self, risk_manager):
        self.risk = risk_manager

    def evaluate(
        self,
        symbol: str,
        signal: dict,
        snapshot: dict,
        analysis: dict,
        env_passed: bool,
        env_analysis: dict,
        env_score: float,
    ) -> PipelineDecision:
        composite_score = float(analysis.get("score") or 0)

        if not env_passed:
            return PipelineDecision(
                ok=False,
                action="env_reject",
                reason=env_analysis.get("verdict") or "environment rejected",
                score=env_score,
                details={"env_analysis": env_analysis},
            )

        score_reason = self._score_reject_reason(analysis)
        if score_reason:
            return PipelineDecision(
                ok=False,
                action="score_reject",
                reason=score_reason,
                score=composite_score,
            )

        veto_reason = self._entry_veto_reason(signal, analysis, snapshot)
        if veto_reason:
            return PipelineDecision(
                ok=False,
                action="entry_veto",
                reason=veto_reason,
                score=composite_score,
            )

        quality, passed_count, quality_notes = self.risk.evaluate_entry_quality(
            symbol, signal, analysis
        )
        signal["entry_quality"] = quality
        signal["entry_quality_notes"] = quality_notes
        signal["entry_quality_passed"] = passed_count

        if passed_count < ENTRY_QUALITY_MIN_PASSED or composite_score < ENTRY_QUALITY_MIN_SCORE:
            return PipelineDecision(
                ok=False,
                action="quality_reject",
                reason=f"quality={quality}, passed={passed_count}, score={composite_score}",
                score=composite_score,
                details={"quality": quality, "passed": passed_count, "notes": quality_notes},
            )

        allowed, risk_reason = self.risk.check_account_risk(symbol)
        if not allowed:
            return PipelineDecision(
                ok=False,
                action="risk_reject",
                reason=risk_reason,
                score=composite_score,
            )

        return PipelineDecision(
            ok=True,
            action="candidate_ok",
            reason="passed pre-agent pipeline",
            score=composite_score,
            details={"quality": quality, "passed": passed_count},
        )

    @staticmethod
    def _score_reject_reason(analysis: dict) -> str | None:
        score = float(analysis.get("score") or 0)
        tags = set(analysis.get("tags") or [])
        hard_tags = {
            "no_price",
            "snapshot_error",
            "price_overheated",
            "funding_hot",
            "long_crowded",
        }
        if score < 43:
            return f"score={score} < 43"
        overlap = tags & hard_tags
        if overlap:
            return f"hard_tags={','.join(sorted(overlap))}"
        return None

    @staticmethod
    def _entry_veto_reason(signal: dict, analysis: dict, snapshot: dict) -> str | None:
        verdict = analysis.get("verdict", "")
        if "过热" in verdict or "杩囩儹" in verdict:
            return f"verdict={verdict}"

        thresholds = load_veto_thresholds()

        change_4h = snapshot.get("change_4h", 0) or 0
        if abs(change_4h) > thresholds["change_4h_pct"]:
            return f"4h change={change_4h}% > {thresholds['change_4h_pct']}%"

        change_24h = snapshot.get("change_24h", 0) or 0
        if abs(change_24h) > thresholds["change_24h_pct"]:
            return f"24h change={change_24h}% > {thresholds['change_24h_pct']}%"

        funding = snapshot.get("funding_rate", 0) or 0
        if abs(funding) >= thresholds["funding_pct"]:
            return f"funding={funding}% >= {thresholds['funding_pct']}%"

        global_lsr = snapshot.get("global_lsr", 1.0) or 1.0
        if global_lsr >= thresholds["lsr_pct"]:
            return f"retail LSR={global_lsr} >= {thresholds['lsr_pct']}"

        taker_ratio = snapshot.get("taker_ratio", 1.0) or 1.0
        if taker_ratio >= thresholds["taker_ratio"]:
            return f"taker ratio={taker_ratio} >= {thresholds['taker_ratio']}"

        direction = signal.get("direction")
        taker_trend = snapshot.get("taker_trend_pct", 0) or 0
        if direction == "long" and taker_trend <= thresholds["long_taker_trend_pct"]:
            return f"long taker trend={taker_trend}% <= {thresholds['long_taker_trend_pct']}%"
        if direction == "short" and taker_trend >= thresholds["short_taker_trend_pct"]:
            return f"short taker trend={taker_trend}% >= {thresholds['short_taker_trend_pct']}%"

        return None


def load_veto_thresholds() -> dict:
    """Load optimizer-managed pipeline thresholds from state.json."""
    thresholds = dict(DEFAULT_VETO_THRESHOLDS)
    if not STATE_PATH.exists():
        return thresholds
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            state = json.load(f)
        overrides = state.get("veto_thresholds") or {}
    except Exception:
        return thresholds
    for key, value in overrides.items():
        if key in thresholds:
            try:
                thresholds[key] = float(value)
            except (TypeError, ValueError):
                continue
    return thresholds
