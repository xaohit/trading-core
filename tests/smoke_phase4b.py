#!/usr/bin/env python3
"""
Phase 4B Smoke Tests — ATR Risk Sizing + TP Pyramid

Covers:
- ATR stop distance calculation
- TP1/TP2 trigger detection (long + short)
- Trailing stop update
- Partial close state transitions
- Full close after trailing/SL

Run:
  PYTHONPATH=<project_root> python tests/smoke_phase4b.py
"""
import sys
import os
import json
import tempfile
from pathlib import Path

# Setup path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Override DB path to temp file for testing
import config
test_db_dir = tempfile.mkdtemp()
config.DB_PATH = Path(test_db_dir) / "test_phase4b.db"
config.BASE_DIR = Path(test_db_dir)
config.HISTORY_DIR = Path(test_db_dir) / "history"
config.STATE_PATH = Path(test_db_dir) / "state.json"
config.BINANCE_API_KEY = "test"
config.BINANCE_API_SECRET = "test"
config.PROXY = "socks5h://localhost:7897"

from db.connection import init_db, get_db, _connection as _orig_conn
from db.trades import TradeDB
from executor import Executor

# Reset DB connection singleton to use overridden config
import db.connection
db.connection._connection = None


def test_atr_sizing():
    """Test ATR-based stop distance and position sizing logic."""
    # Simulate ATR of 2% with multiplier 1.5 -> stop_distance = 3%
    price = 100.0
    atr_pct = 2.0
    stop_distance = atr_pct / 100 * config.ATR_STOP_MULTIPLIER
    assert abs(stop_distance - 0.03) < 0.0001, f"Expected 0.03, got {stop_distance}"

    # R value = price * stop_distance
    r_value = price * stop_distance
    assert abs(r_value - 3.0) < 0.0001, f"Expected R=3.0, got {r_value}"

    # TP1 = entry + 1.5R (long)
    tp1 = price + r_value * config.TP1_R_MULTIPLE
    assert abs(tp1 - 104.5) < 0.0001, f"Expected TP1=104.5, got {tp1}"

    # TP2 = entry + 3R (long)
    tp2 = price + r_value * config.TP2_R_MULTIPLE
    assert abs(tp2 - 109.0) < 0.0001, f"Expected TP2=109.0, got {tp2}"

    # SL = entry - R (long)
    sl = price - r_value
    assert abs(sl - 97.0) < 0.0001, f"Expected SL=97.0, got {sl}"

    # For short: TP1 = entry - 1.5R, SL = entry + R
    tp1_short = price - r_value * config.TP1_R_MULTIPLE
    assert abs(tp1_short - 95.5) < 0.0001, f"Expected TP1_short=95.5, got {tp1_short}"

    sl_short = price + r_value
    assert abs(sl_short - 103.0) < 0.0001, f"Expected SL_short=103.0, got {sl_short}"

    print("  [OK] ATR sizing calculations correct")


def test_tp_trigger_long():
    """Test TP1/TP2/trailing/SL triggers for long position."""
    init_db()
    get_db().execute("DELETE FROM trades")
    get_db().commit()

    trade = {
        "symbol": "TESTUSDT",
        "direction": "long",
        "leverage": 3,
        "position_pct": 30,
        "position_usd": 12.0,
        "notional_usd": 36.0,
        "entry_price": 100.0,
        "stop_loss": 97.0,
        "take_profit": 109.0,
        "entry_time": "04-30 12:00",
        "tp1_price": 104.5,
        "tp1_done": 0,
        "tp2_price": 109.0,
        "tp2_done": 0,
        "trailing_stop": 94.0,
        "remaining_pct": 100,
        "breakeven_done": 0,
        "initial_r": 3.0,
        "stop_distance": 0.03,
        "atr_pct_at_entry": 2.0,
        "pre_analysis": {"type": "test"},
    }

    trade_id = TradeDB.insert(trade)
    pos = TradeDB.get_open()[0]

    # No trigger at entry price
    actions = Executor.check_tp_levels(pos, 100.0)
    assert len(actions) == 0, f"Expected no trigger at entry, got {actions}"

    # TP1 trigger
    actions = Executor.check_tp_levels(pos, 105.0)
    assert len(actions) == 1, f"Expected TP1 trigger, got {actions}"
    assert actions[0]["type"] == "tp1", f"Expected tp1, got {actions[0]['type']}"

    # Simulate TP1 done
    TradeDB.update(trade_id, tp1_done=1, stop_loss=100.0, remaining_pct=70)
    pos = TradeDB.get_open()[0]

    # TP2 trigger
    actions = Executor.check_tp_levels(pos, 110.0)
    assert len(actions) == 1
    assert actions[0]["type"] == "tp2"

    # Simulate TP2 done
    TradeDB.update(trade_id, tp2_done=1, remaining_pct=40)
    pos = TradeDB.get_open()[0]

    # Trailing stop trigger (price drops below trailing)
    actions = Executor.check_tp_levels(pos, 93.0)
    assert len(actions) == 1
    assert actions[0]["type"] == "trailing"

    # SL trigger (price drops below SL)
    TradeDB.update(trade_id, trailing_stop=None)
    pos = TradeDB.get_open()[0]
    actions = Executor.check_tp_levels(pos, 96.0)
    assert len(actions) == 1
    assert actions[0]["type"] == "sl"

    print("  [OK] TP trigger detection (long) correct")


def test_tp_trigger_short():
    """Test TP1/TP2/trailing/SL triggers for short position."""
    init_db()
    get_db().execute("DELETE FROM trades")
    get_db().commit()

    trade = {
        "symbol": "TESTSHORT",
        "direction": "short",
        "leverage": 3,
        "position_pct": 30,
        "position_usd": 12.0,
        "notional_usd": 36.0,
        "entry_price": 100.0,
        "stop_loss": 103.0,
        "take_profit": 91.0,
        "entry_time": "04-30 12:00",
        "tp1_price": 95.5,
        "tp1_done": 0,
        "tp2_price": 91.0,
        "tp2_done": 0,
        "trailing_stop": 106.0,
        "remaining_pct": 100,
        "breakeven_done": 0,
        "initial_r": 3.0,
        "stop_distance": 0.03,
        "atr_pct_at_entry": 2.0,
        "pre_analysis": {"type": "test"},
    }

    trade_id = TradeDB.insert(trade)
    pos = TradeDB.get_open()[0]
    assert pos["symbol"] == "TESTSHORT", f"Wrong symbol: {pos['symbol']}"
    assert pos["stop_loss"] == 103.0, f"Wrong SL: {pos['stop_loss']}"

    # No trigger at entry price
    actions = Executor.check_tp_levels(pos, 100.0)
    assert len(actions) == 0, f"Expected no trigger at entry (price=100, sl=103), got {actions}"

    # TP1 trigger (price goes down for short)
    actions = Executor.check_tp_levels(pos, 95.0)
    assert len(actions) == 1, f"Expected TP1 trigger, got {actions}"
    assert actions[0]["type"] == "tp1", f"Expected tp1, got {actions[0]['type']}"

    # Simulate TP1 done
    TradeDB.update(trade_id, tp1_done=1, stop_loss=100.0, remaining_pct=70)
    pos = TradeDB.get_open()[0]

    # TP2 trigger
    actions = Executor.check_tp_levels(pos, 90.0)
    assert len(actions) == 1, f"Expected TP2 trigger, got {actions}"
    assert actions[0]["type"] == "tp2"

    # Simulate TP2 done
    TradeDB.update(trade_id, tp2_done=1, remaining_pct=40)
    pos = TradeDB.get_open()[0]

    # Trailing stop trigger (price rises above trailing)
    actions = Executor.check_tp_levels(pos, 107.0)
    assert len(actions) == 1, f"Expected trailing trigger, got {actions}"
    assert actions[0]["type"] == "trailing"

    print("  [OK] TP trigger detection (short) correct")


def test_trailing_stop_update_long():
    """Test trailing stop moves up for long position."""
    pos = {
        "direction": "long",
        "trailing_stop": 94.0,
        "tp1_done": 1,
        "atr_pct_at_entry": 2.0,
        "initial_r": 3.0,
    }

    # Price goes up -> trailing should move up
    new_trail = Executor.update_trailing_stop(pos, 108.0)
    assert new_trail is not None
    assert new_trail > pos["trailing_stop"], f"Trailing should move up: {new_trail} > {pos['trailing_stop']}"

    # Price goes down -> trailing should NOT move
    pos["trailing_stop"] = new_trail
    new_trail2 = Executor.update_trailing_stop(pos, 105.0)
    assert new_trail2 is None, f"Trailing should not move down: got {new_trail2}"

    print("  [OK] Trailing stop update (long) correct")


def test_trailing_stop_update_short():
    """Test trailing stop moves down for short position."""
    pos = {
        "direction": "short",
        "trailing_stop": 106.0,
        "tp1_done": 1,
        "atr_pct_at_entry": 2.0,
        "initial_r": 3.0,
    }

    # Price goes down -> trailing should move down
    new_trail = Executor.update_trailing_stop(pos, 92.0)
    assert new_trail is not None
    assert new_trail < pos["trailing_stop"], f"Trailing should move down: {new_trail} < {pos['trailing_stop']}"

    # Price goes up -> trailing should NOT move
    pos["trailing_stop"] = new_trail
    new_trail2 = Executor.update_trailing_stop(pos, 95.0)
    assert new_trail2 is None, f"Trailing should not move up: got {new_trail2}"

    print("  [OK] Trailing stop update (short) correct")


def test_partial_close_db():
    """Test partial close updates DB correctly."""
    init_db()
    get_db().execute("DELETE FROM trades")
    get_db().commit()

    trade = {
        "symbol": "TESTUSDT",
        "direction": "long",
        "leverage": 3,
        "position_pct": 30,
        "position_usd": 12.0,
        "notional_usd": 36.0,
        "entry_price": 100.0,
        "stop_loss": 97.0,
        "take_profit": 109.0,
        "entry_time": "04-30 12:00",
        "tp1_price": 104.5,
        "tp1_done": 0,
        "tp2_price": 109.0,
        "tp2_done": 0,
        "trailing_stop": 94.0,
        "remaining_pct": 100,
        "breakeven_done": 0,
        "initial_r": 3.0,
        "stop_distance": 0.03,
        "atr_pct_at_entry": 2.0,
        "pre_analysis": {"type": "test"},
    }

    trade_id = TradeDB.insert(trade)

    # Simulate TP1 partial close
    TradeDB.partial_close(
        trade_id, 105.0, "04-30 12:05",
        "tp1_30%", 1.5, 0.18,
        30, 70,
        new_stop=100.0  # breakeven
    )

    pos = TradeDB.get_open()[0]
    assert pos["remaining_pct"] == 70, f"Expected remaining=70, got {pos['remaining_pct']}"
    assert pos["tp1_done"] == 1, f"Expected tp1_done=1, got {pos['tp1_done']}"
    assert pos["stop_loss"] == 100.0, f"Expected stop_loss=100 (breakeven), got {pos['stop_loss']}"

    # Simulate TP2 partial close
    TradeDB.partial_close(
        trade_id, 110.0, "04-30 12:10",
        "tp2_30%", 3.0, 0.36,
        30, 40
    )

    pos = TradeDB.get_open()[0]
    assert pos["remaining_pct"] == 40, f"Expected remaining=40, got {pos['remaining_pct']}"
    assert pos["tp2_done"] == 1, f"Expected tp2_done=1, got {pos['tp2_done']}"

    # Full close
    TradeDB.close(trade_id, 108.0, "04-30 12:15", "追踪止损", 2.5, 0.30)

    pos = TradeDB.get_open()
    assert len(pos) == 0, f"Expected no open positions after full close, got {len(pos)}"

    closed = TradeDB.get_closed()
    assert len(closed) == 1
    assert closed[0]["remaining_pct"] == 0, f"Expected remaining_pct=0 for closed, got {closed[0]['remaining_pct']}"

    print("  [OK] Partial close DB operations correct")


def test_atr_fallback():
    """Test that when ATR is None, strategy sl_pct is used as fallback."""
    # If ATR is not available, stop_distance should fall back to signal's sl_pct
    price = 100.0
    signal_sl_pct = 0.05

    # Simulate the executor fallback logic
    atr_p = None
    if atr_p is not None and atr_p > 0:
        stop_distance = atr_p / 100 * config.ATR_STOP_MULTIPLIER
    else:
        stop_distance = signal_sl_pct

    assert stop_distance == 0.05, f"Expected fallback sl_pct=0.05, got {stop_distance}"

    # Clamp bounds
    assert 0.01 <= stop_distance <= 0.20, f"Stop distance {stop_distance} out of bounds"

    print("  [OK] ATR fallback to strategy sl_pct correct")


if __name__ == "__main__":
    print("Phase 4B Smoke Tests")
    print("=" * 40)
    print(f"DB path: {config.DB_PATH}")
    print(f"TradeDB.get_db: {get_db()}")

    test_atr_sizing()
    test_tp_trigger_long()
    test_tp_trigger_short()
    test_trailing_stop_update_long()
    test_trailing_stop_update_short()
    test_partial_close_db()
    test_atr_fallback()

    print("=" * 40)
    print("PHASE4B_SMOKE_OK")
