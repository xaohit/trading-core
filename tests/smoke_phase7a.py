"""
Phase 7A Risk Hardening Smoke Tests
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    MAX_DAILY_LOSS_PCT, MAX_DAILY_TRADES, COOLDOWN_AFTER_LOSS_MINUTES,
    SECTOR_MAX_CONCENTRATION, ENTRY_QUALITY_MIN_PASSED, ENTRY_QUALITY_MIN_SCORE,
)
from risk import RiskManager
from state import State


def test_config_params():
    """Test new config params exist and have correct values."""
    assert MAX_DAILY_LOSS_PCT == 5, f"MAX_DAILY_LOSS_PCT should be 5, got {MAX_DAILY_LOSS_PCT}"
    assert MAX_DAILY_TRADES == 15, f"MAX_DAILY_TRADES should be 15, got {MAX_DAILY_TRADES}"
    assert COOLDOWN_AFTER_LOSS_MINUTES == 30, f"Cooldown should be 30min, got {COOLDOWN_AFTER_LOSS_MINUTES}"
    assert SECTOR_MAX_CONCENTRATION == 2, f"SECTOR_MAX_CONCENTRATION should be 2, got {SECTOR_MAX_CONCENTRATION}"
    assert ENTRY_QUALITY_MIN_PASSED == 4, f"Min passed should be 4, got {ENTRY_QUALITY_MIN_PASSED}"
    assert ENTRY_QUALITY_MIN_SCORE == 50, f"Min score should be 50, got {ENTRY_QUALITY_MIN_SCORE}"

    print("  [OK] Config params correct")


def test_sector_map():
    """Test SECTOR_MAP covers expected tokens."""
    assert "BTCUSDT" in RiskManager.SECTOR_MAP["majors"]
    assert "ETHUSDT" in RiskManager.SECTOR_MAP["majors"]
    assert "DOGEUSDT" in RiskManager.SECTOR_MAP["meme"]
    assert "SOLUSDT" in RiskManager.SECTOR_MAP["alt_l1"]
    assert "FETUSDT" in RiskManager.SECTOR_MAP["ai"]
    assert "UNIUSDT" in RiskManager.SECTOR_MAP["defi"]

    # Unknown tokens go to "other"
    rm = RiskManager(State())
    assert rm._get_sector("BTCUSDT") == "majors"
    assert rm._get_sector("DOGEUSDT") == "meme"
    assert rm._get_sector("XYZUSDT") == "other"

    print("  [OK] Sector map correct")


def test_sector_concentration():
    """Test sector concentration check."""
    rm = RiskManager(State())

    # Check that same sector tokens map to same sector
    assert rm._get_sector("ARBUSDT") == "l2"
    assert rm._get_sector("OPUSDT") == "l2"

    # Check that "other" is not in any sector
    assert rm._get_sector("RANDOMUSDT") == "other"

    print("  [OK] Sector concentration check correct")


def test_entry_quality_veto():
    """Test hard veto conditions from scanner."""
    # Import Scanner for the static method
    try:
        from .scanner import Scanner
    except ImportError:
        from scanner import Scanner

    # No veto — clean signal
    analysis = {"verdict": "✅ 看起来健康", "score": 70, "tags": ["liquid"]}
    snapshot = {
        "change_4h": 5.0,
        "change_24h": 15.0,
        "funding_rate": 0.01,
        "global_lsr": 1.2,
        "taker_ratio": 1.3,
        "taker_trend_pct": 2.0,
    }
    assert Scanner._entry_quality_veto(analysis, snapshot) is None, "Should pass clean signal"

    # Veto: overheated verdict
    analysis_hot = {"verdict": "⚠ 过热预警", "score": 70}
    assert Scanner._entry_quality_veto(analysis_hot, snapshot) is not None, "Should veto overheated"

    # Veto: 4h change > 25%
    analysis_ok = {"verdict": "✅ 看起来健康", "score": 70}
    snapshot_4h = {**snapshot, "change_4h": 30.0}
    assert Scanner._entry_quality_veto(analysis_ok, snapshot_4h) is not None, "Should veto 4h > 25%"

    # Veto: 24h change > 50%
    snapshot_24h = {**snapshot, "change_24h": 60.0}
    assert Scanner._entry_quality_veto(analysis_ok, snapshot_24h) is not None, "Should veto 24h > 50%"

    # Veto: funding >= 0.05%
    snapshot_fund = {**snapshot, "funding_rate": 0.06}
    assert Scanner._entry_quality_veto(analysis_ok, snapshot_fund) is not None, "Should veto high funding"

    # Veto: retail LSR >= 1.7
    snapshot_lsr = {**snapshot, "global_lsr": 1.8}
    assert Scanner._entry_quality_veto(analysis_ok, snapshot_lsr) is not None, "Should veto high LSR"

    # Veto: taker ratio >= 1.8
    snapshot_taker = {**snapshot, "taker_ratio": 1.9}
    assert Scanner._entry_quality_veto(analysis_ok, snapshot_taker) is not None, "Should veto high taker ratio"

    # Veto: taker trend <= -5%
    snapshot_trend = {**snapshot, "taker_trend_pct": -6.0}
    assert Scanner._entry_quality_veto(analysis_ok, snapshot_trend) is not None, "Should veto weak taker trend"

    print("  [OK] Entry quality veto correct")


def test_entry_quality_evaluation():
    """Test 7-item entry quality gate."""
    rm = RiskManager(State())

    # Good entry
    market_good = {
        "score": 70,
        "change_15m": 0.5,
        "change_1h": 2.0,
        "oi_15m_change": 3.0,
        "oi_1h_change": 2.0,
        "taker_ratio": 1.2,
    }
    signal_good = {"direction": "long", "funding_rate": 0.01}
    quality, passed, notes = rm.evaluate_entry_quality("BTCUSDT", signal_good, market_good)
    assert quality == "FULL", f"Should be FULL quality, got {quality}"
    assert passed >= 6, f"Should pass >= 6 items, got {passed}"

    # Poor entry
    market_bad = {
        "score": 30,
        "change_15m": 5.0,
        "change_1h": -3.0,
        "oi_15m_change": -2.0,
        "oi_1h_change": -1.0,
        "taker_ratio": 2.0,
    }
    signal_bad = {"direction": "long", "funding_rate": 0.10}
    quality2, passed2, notes2 = rm.evaluate_entry_quality("DOGEUSDT", signal_bad, market_bad)
    assert quality2 == "SKIP", f"Should be SKIP quality, got {quality2}"
    assert passed2 <= 2, f"Should pass <= 2 items, got {passed2}"

    print("  [OK] Entry quality evaluation correct")


if __name__ == "__main__":
    print("Phase 7A Risk Hardening Smoke Tests")
    print("=" * 40)

    tmpdir = tempfile.mkdtemp()
    os.environ["HOME"] = tmpdir

    test_config_params()
    test_sector_map()
    test_sector_concentration()
    test_entry_quality_veto()
    test_entry_quality_evaluation()

    print("=" * 40)
    print("PHASE7A_SMOKE_OK")
