"""
Decision pipeline.

Keeps candidate filtering in one place so the scanner can stay focused on
orchestration: discover signals, rank candidates, execute approved trades.
"""
from __future__ import annotations

from dataclasses import dataclass, field

try:
    from .config import ENTRY_QUALITY_MIN_PASSED, ENTRY_QUALITY_MIN_SCORE
except ImportError:
    from config import ENTRY_QUALITY_MIN_PASSED, ENTRY_QUALITY_MIN_SCORE


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

        veto_reason = self._entry_veto_reason(analysis, snapshot)
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
    def _entry_veto_reason(analysis: dict, snapshot: dict) -> str | None:
        verdict = analysis.get("verdict", "")
        if "过热" in verdict or "杩囩儹" in verdict:
            return f"verdict={verdict}"

        change_4h = snapshot.get("change_4h", 0) or 0
        if abs(change_4h) > 25:
            return f"4h change={change_4h}% > 25%"

        change_24h = snapshot.get("change_24h", 0) or 0
        if abs(change_24h) > 50:
            return f"24h change={change_24h}% > 50%"

        funding = snapshot.get("funding_rate", 0) or 0
        if abs(funding) >= 0.05:
            return f"funding={funding}% >= 0.05%"

        global_lsr = snapshot.get("global_lsr", 1.0) or 1.0
        if global_lsr >= 1.7:
            return f"retail LSR={global_lsr} >= 1.7"

        taker_ratio = snapshot.get("taker_ratio", 1.0) or 1.0
        if taker_ratio >= 1.8:
            return f"taker ratio={taker_ratio} >= 1.8"

        taker_trend = snapshot.get("taker_trend_pct", 0) or 0
        if taker_trend <= -5:
            return f"taker trend={taker_trend}% <= -5%"

        return None
