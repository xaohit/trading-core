"""
Phase 6 Reflection Engine Smoke Tests
"""
import os
import sys
import tempfile
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reflection import (
    FailureArchive, StrategyWeighter, RuleReflector,
    FAILURE_TAG_DEFS, WEIGHT_LOOKBACK_TRADES,
)
from db.connection import init_db, get_db


def setup_test_db():
    """Initialize test database with sample trades."""
    init_db()
    conn = get_db()
    c = conn.cursor()

    # Clear existing data
    c.execute("DELETE FROM trades")
    c.execute("DELETE FROM failure_archive")
    conn.commit()

    now = int(time.time())
    sample_trades = [
        # Neg funding longs — mixed results
        (1, "DOGEUSDT", "long", 0.0850, 0.0820, -3.53, "止损",
         json.dumps({"type": "neg_funding_long", "snapshot": {
             "change_15m": 0.5, "change_1h": 1.2, "funding_rate": -0.02,
             "global_lsr": 0.8, "atr_pct": 1.5, "oi_15m_change": 2.0,
             "oi_1h_change": 1.0, "taker_ratio": 1.2,
         }})),
        (2, "DOGEUSDT", "long", 0.0860, 0.0840, -2.33, "止损",
         json.dumps({"type": "neg_funding_long", "snapshot": {
             "change_15m": 3.0, "change_1h": 6.0, "funding_rate": 0.06,
             "global_lsr": 1.8, "atr_pct": 1.5, "oi_15m_change": -1.0,
             "oi_1h_change": -0.5, "taker_ratio": 0.7,
         }})),
        (3, "DOGEUSDT", "long", 0.0870, 0.0920, 5.75, "止盈",
         json.dumps({"type": "neg_funding_long", "snapshot": {
             "change_15m": 0.2, "change_1h": 0.8, "funding_rate": -0.01,
             "global_lsr": 0.7, "atr_pct": 1.5, "oi_15m_change": 3.0,
             "oi_1h_change": 2.0, "taker_ratio": 1.5,
         }})),
        (4, "SOLUSDT", "long", 95.0, 92.0, -3.16, "止损",
         json.dumps({"type": "crash_bounce_long", "snapshot": {
             "change_15m": -2.0, "change_1h": -5.0, "funding_rate": -0.03,
             "global_lsr": 0.6, "atr_pct": 2.0, "oi_15m_change": -2.0,
             "oi_1h_change": -1.5, "taker_ratio": 0.5,
         }})),
        (5, "SOLUSDT", "long", 93.0, 98.0, 5.38, "止盈",
         json.dumps({"type": "crash_bounce_long", "snapshot": {
             "change_15m": -1.0, "change_1h": -3.0, "funding_rate": -0.02,
             "global_lsr": 0.7, "atr_pct": 2.0, "oi_15m_change": 1.0,
             "oi_1h_change": 0.5, "taker_ratio": 1.1,
         }})),
        # Pos funding shorts
        (6, "AVAXUSDT", "short", 25.0, 26.5, -6.0, "止损",
         json.dumps({"type": "pos_funding_short", "snapshot": {
             "change_15m": 4.0, "change_1h": 8.0, "funding_rate": 0.08,
             "global_lsr": 1.9, "atr_pct": 2.5, "oi_15m_change": 5.0,
             "oi_1h_change": 3.0, "taker_ratio": 1.8,
         }})),
        (7, "AVAXUSDT", "short", 25.5, 24.0, 5.88, "止盈",
         json.dumps({"type": "pos_funding_short", "snapshot": {
             "change_15m": 1.0, "change_1h": 2.0, "funding_rate": 0.04,
             "global_lsr": 1.2, "atr_pct": 2.5, "oi_15m_change": 1.0,
             "oi_1h_change": 0.5, "taker_ratio": 0.9,
         }})),
        # Pump shorts
        (8, "MATICUSDT", "short", 0.55, 0.58, -5.45, "止损",
         json.dumps({"type": "pump_short", "snapshot": {
             "change_15m": 8.0, "change_1h": 15.0, "funding_rate": 0.10,
             "global_lsr": 2.0, "atr_pct": 3.0, "oi_15m_change": 10.0,
             "oi_1h_change": 8.0, "taker_ratio": 2.5,
         }})),
    ]

    for trade_id, symbol, direction, entry, exit_p, pnl, reason, pre in sample_trades:
        c.execute(
            """INSERT INTO trades
            (id, symbol, direction, entry_price, exit_price, pnl_pct,
             exit_reason, status, pre_analysis, entry_time, exit_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'closed', ?, ?, ?)""",
            (trade_id, symbol, direction, entry, exit_p, pnl, reason, pre,
             "04-25 10:00", "04-25 12:00"),
        )
    conn.commit()


def test_failure_tagging():
    """Test failure tag analysis."""
    trade = {
        "id": 1,
        "symbol": "DOGEUSDT",
        "direction": "long",
        "entry_price": 0.0850,
        "stop_loss": 0.0820,
        "status": "closed",
        "pnl_pct": -3.53,
        "pre_analysis": {
            "type": "neg_funding_long",
            "verdict": "中性",
            "snapshot": {
                "change_15m": 3.0,
                "change_1h": 6.0,
                "funding_rate": 0.06,
                "global_lsr": 1.8,
                "atr_pct": 1.5,
                "oi_15m_change": -1.0,
                "oi_1h_change": -0.5,
                "taker_ratio": 0.7,
            },
        },
    }

    exit_snapshot = {
        "oi_15m_change": -2.0,
        "oi_1h_change": -1.5,
        "oi_4h_change": -0.5,
        "taker_ratio": 0.6,
    }

    tags = FailureArchive.analyze_failure(trade, exit_snapshot=exit_snapshot)
    assert "entry_not_healthy" in tags, f"Should tag unhealthy entry. Got: {tags}"
    assert "entry_15m_hot" in tags, f"Should tag hot 15m. Got: {tags}"
    assert "entry_1h_hot" in tags, f"Should tag hot 1h. Got: {tags}"
    assert "entry_funding_hot" in tags, f"Should tag hot funding. Got: {tags}"
    assert "entry_lsr_hot" in tags, f"Should tag hot LSR. Got: {tags}"
    assert "oi15_reversed" in tags, f"Should tag OI 15m reversal. Got: {tags}"
    assert "oi1h_reversed" in tags, f"Should tag OI 1h reversal. Got: {tags}"
    assert "buy_pressure_faded" in tags, f"Should tag faded pressure. Got: {tags}"

    print("  [OK] Failure tagging correct")


def test_failure_archive():
    """Test archiving failures to DB."""
    # Archive trade 1 (loss)
    result = FailureArchive.archive(1, exit_reason="止损")
    assert result["ok"], f"Archive should succeed: {result}"
    assert len(result["tags"]) > 0, "Should have failure tags"

    # Archive trade 2 (loss)
    result2 = FailureArchive.archive(2, exit_reason="止损")
    assert result2["ok"], f"Archive should succeed: {result2}"

    # Archive trade 4 (loss)
    result4 = FailureArchive.archive(4, exit_reason="止损")
    assert result4["ok"], f"Archive should succeed: {result4}"

    # Archive trade 6 (loss)
    result6 = FailureArchive.archive(6, exit_reason="止损")
    assert result6["ok"], f"Archive should succeed: {result6}"

    # Archive trade 8 (loss)
    result8 = FailureArchive.archive(8, exit_reason="止损")
    assert result8["ok"], f"Archive should succeed: {result8}"

    # Check tag stats
    tag_stats = FailureArchive.get_tag_stats()
    assert len(tag_stats) > 0, "Should have tag stats"

    # Verify tags are sorted by count
    if len(tag_stats) > 1:
        assert tag_stats[0]["count"] >= tag_stats[-1]["count"], \
            "Tags should be sorted by count descending"

    print("  [OK] Failure archive correct")


def test_strategy_weights():
    """Test adaptive strategy weight calculation."""
    weights = StrategyWeighter.get_weights()

    # All strategies should have weights
    assert len(weights) > 0, "Should have weights for strategies"

    # Weights should sum to ~1.0
    total = sum(weights.values())
    assert 0.95 <= total <= 1.05, f"Weights should sum to ~1.0, got {total}"

    # All weights should be >= minimum
    for s, w in weights.items():
        assert w >= 0.05, f"Weight for {s} should be >= 0.05, got {w}"

    # Check priority ranking
    priority = StrategyWeighter.get_strategy_priority()
    assert len(priority) > 0, "Should have priority ranking"

    # Should be sorted by weight descending
    for i in range(len(priority) - 1):
        assert priority[i]["weight"] >= priority[i + 1]["weight"], \
            "Priority should be sorted by weight descending"

    print("  [OK] Strategy weights correct")


def test_rule_reflection():
    """Test rule suggestion generation."""
    suggestions = RuleReflector.get_suggestions(min_frequency=1)

    # Should generate suggestions from archived failures
    assert len(suggestions) > 0, f"Should have rule suggestions. Tags: {FailureArchive.get_tag_stats()}"

    # Each suggestion should have required fields
    for s in suggestions:
        assert "tag" in s, f"Suggestion should have 'tag': {s}"
        assert "count" in s, f"Suggestion should have 'count': {s}"
        assert "suggested_action" in s, f"Suggestion should have 'suggested_action': {s}"
        assert "confidence" in s, f"Suggestion should have 'confidence': {s}"
        assert 0 <= s["confidence"] <= 1.0, f"Confidence should be 0-1: {s['confidence']}"

    # Test applying a suggestion
    if suggestions:
        applied = RuleReflector.apply_suggestion(suggestions[0])
        assert applied, "Should be able to apply suggestion"

    print("  [OK] Rule reflection correct")


def test_no_false_tags_on_wins():
    """Test that winning trades don't get failure tags."""
    # Trade 3 was a win — should not be archived as failure
    trade = {
        "id": 3,
        "symbol": "DOGEUSDT",
        "direction": "long",
        "entry_price": 0.0870,
        "exit_price": 0.0920,
        "stop_loss": 0.0840,
        "status": "closed",
        "pnl_pct": 5.75,
        "pre_analysis": {
            "type": "neg_funding_long",
            "verdict": "✅ 看起来健康",
            "snapshot": {
                "change_15m": 0.2,
                "change_1h": 0.8,
                "funding_rate": -0.01,
                "global_lsr": 0.7,
                "atr_pct": 1.5,
                "oi_15m_change": 3.0,
                "oi_1h_change": 2.0,
                "taker_ratio": 1.5,
            },
        },
    }

    # Even if analyzed, a healthy trade shouldn't get failure tags
    # (though analyze_failure doesn't check pnl, the archive is only called on losses)
    tags = FailureArchive.analyze_failure(trade)
    assert "entry_not_healthy" not in tags, "Healthy entry should not be tagged"
    assert "entry_15m_hot" not in tags, "Normal 15m should not be tagged"

    print("  [OK] No false tags on healthy trades")


if __name__ == "__main__":
    print("Phase 6 Reflection Engine Smoke Tests")
    print("=" * 40)

    # Use temp DB
    tmpdir = tempfile.mkdtemp()
    os.environ["HOME"] = tmpdir

    setup_test_db()

    test_failure_tagging()
    test_failure_archive()
    test_strategy_weights()
    test_rule_reflection()
    test_no_false_tags_on_wins()

    print("=" * 40)
    print("PHASE6_SMOKE_OK")
