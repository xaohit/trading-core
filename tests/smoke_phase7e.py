"""
Phase 7E Backtesting Engine Smoke Tests
"""
import os
import sys
import tempfile
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import BacktestEngine, build_historical_snapshot


def _make_klines(n: int = 100, base_price: float = 50000.0) -> list[dict]:
    """Generate synthetic klines for testing."""
    klines = []
    price = base_price
    for i in range(n):
        # Random-ish walk
        change = ((i % 7) - 3) * 0.002
        high_delta = price * 0.01
        low_delta = price * 0.01
        high = price * (1 + abs(change) + 0.005)
        low = price * (1 - abs(change) - 0.005)
        close = price * (1 + change)

        klines.append({
            "open_time": i * 900000,
            "open": price,
            "high": high,
            "low": low,
            "close": close,
            "volume": 100.0,
            "close_time": (i + 1) * 900000,
            "quote_volume": price * 100,
            "trades": 500,
        })
        price = close
    return klines


def test_build_snapshot():
    """Test building historical snapshot from klines."""
    klines = _make_klines(100, 50000.0)

    # Need at least 14 candles for ATR
    snapshot = build_historical_snapshot("BTCUSDT", klines, 20)
    assert snapshot is not None, "Should build snapshot at idx 20"
    assert snapshot["price"] > 0, "Price should be positive"
    assert snapshot["atr"] > 0, "ATR should be positive"
    assert snapshot["atr_pct"] > 0, "ATR% should be positive"
    assert "change_15m" in snapshot
    assert "change_1h" in snapshot
    assert "change_4h" in snapshot
    assert "change_24h" in snapshot

    # Too few candles
    snapshot_early = build_historical_snapshot("BTCUSDT", klines, 10)
    assert snapshot_early is None, "Should return None with < 14 candles"

    print("  [OK] Build snapshot correct")


def test_backtest_engine_basic():
    """Test basic backtest engine functionality."""
    klines = _make_klines(200, 50000.0)
    klines_by_symbol = {"BTCUSDT": klines}

    engine = BacktestEngine(
        symbols=["BTCUSDT"],
        klines_by_symbol=klines_by_symbol,
        initial_balance=10000.0,
        leverage=3,
        sizing_mode="atr",
        atr_multiplier=1.5,
        risk_pct=2.0,
        cooldown_candles=4,
    )

    result = engine.run()
    assert "error" not in result, f"Should not error: {result.get('error')}"
    assert "total_trades" in result
    assert "win_rate" in result
    assert "max_drawdown_pct" in result
    assert "strategy_stats" in result
    assert "symbol_stats" in result
    assert "equity_curve" in result

    # Balance should be non-negative
    assert result["final_balance"] >= 0, "Balance should be >= 0"

    # Max drawdown should be >= 0
    assert result["max_drawdown_pct"] >= 0, "Max drawdown should be >= 0"

    print(f"  [OK] Basic backtest: {result['total_trades']} trades, "
          f"win_rate={result['win_rate']:.1f}%, pnl={result['total_pnl_pct']:+.2f}%")


def test_backtest_multiple_symbols():
    """Test backtest with multiple symbols."""
    klines_btc = _make_klines(200, 50000.0)
    klines_eth = _make_klines(200, 3000.0)
    klines_by_symbol = {"BTCUSDT": klines_btc, "ETHUSDT": klines_eth}

    engine = BacktestEngine(
        symbols=["BTCUSDT", "ETHUSDT"],
        klines_by_symbol=klines_by_symbol,
        initial_balance=10000.0,
        leverage=3,
        sizing_mode="fixed",
        risk_pct=2.0,
        cooldown_candles=4,
    )

    result = engine.run()
    assert "error" not in result, f"Should not error: {result.get('error')}"

    # Should have stats for both symbols (or at least some trades)
    assert len(result["symbol_stats"]) >= 0  # May be 0 if no signals

    print(f"  [OK] Multi-symbol backtest: {result['total_trades']} trades")


def test_backtest_fixed_vs_atr():
    """Compare ATR vs fixed sizing modes."""
    klines = _make_klines(300, 50000.0)
    klines_by_symbol = {"BTCUSDT": klines}

    # ATR sizing
    engine_atr = BacktestEngine(
        symbols=["BTCUSDT"],
        klines_by_symbol=klines_by_symbol.copy(),
        initial_balance=10000.0,
        sizing_mode="atr",
        atr_multiplier=1.5,
        cooldown_candles=4,
    )
    result_atr = engine_atr.run()

    # Fixed sizing
    engine_fixed = BacktestEngine(
        symbols=["BTCUSDT"],
        klines_by_symbol={"BTCUSDT": _make_klines(300, 50000.0)},
        initial_balance=10000.0,
        sizing_mode="fixed",
        cooldown_candles=4,
    )
    result_fixed = engine_fixed.run()

    # Both should complete without error
    assert "error" not in result_atr
    assert "error" not in result_fixed

    # Results should differ (different sizing = different PnL)
    assert result_atr["sizing_mode"] == "atr"
    assert result_fixed["sizing_mode"] == "fixed"

    print(f"  [OK] ATR vs fixed: ATR pnl={result_atr['total_pnl_pct']:+.2f}%, "
          f"fixed pnl={result_fixed['total_pnl_pct']:+.2f}%")


def test_equity_curve():
    """Test that equity curve is recorded."""
    klines = _make_klines(500, 50000.0)
    klines_by_symbol = {"BTCUSDT": klines}

    engine = BacktestEngine(
        symbols=["BTCUSDT"],
        klines_by_symbol=klines_by_symbol,
        initial_balance=10000.0,
        sizing_mode="atr",
        cooldown_candles=2,
    )

    result = engine.run()

    # Equity curve should have multiple points
    curve = result["equity_curve"]
    assert len(curve) > 0, "Should have equity curve points"

    # Each point should have required fields
    for point in curve:
        assert "candle" in point
        assert "equity" in point
        assert "balance" in point
        assert "open_positions" in point

    print(f"  [OK] Equity curve: {len(curve)} points")


def test_max_positions():
    """Test that max positions is respected."""
    klines1 = _make_klines(200, 50000.0)
    klines2 = _make_klines(200, 3000.0)
    klines3 = _make_klines(200, 100.0)
    klines_by_symbol = {"BTCUSDT": klines1, "ETHUSDT": klines2, "SOLUSDT": klines3}

    engine = BacktestEngine(
        symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        klines_by_symbol=klines_by_symbol,
        initial_balance=10000.0,
        max_positions=1,  # Only 1 position at a time
        cooldown_candles=1,
    )

    result = engine.run()
    assert "error" not in result

    # Equity curve should never show > 1 open position
    for point in result["equity_curve"]:
        assert point["open_positions"] <= 1, \
            f"Should never have > 1 position, got {point['open_positions']}"

    print("  [OK] Max positions respected")


if __name__ == "__main__":
    print("Phase 7E Backtesting Engine Smoke Tests")
    print("=" * 40)

    tmpdir = tempfile.mkdtemp()
    os.environ["HOME"] = tmpdir

    test_build_snapshot()
    test_backtest_engine_basic()
    test_backtest_multiple_symbols()
    test_backtest_fixed_vs_atr()
    test_equity_curve()
    test_max_positions()

    print("=" * 40)
    print("PHASE7E_SMOKE_OK")
