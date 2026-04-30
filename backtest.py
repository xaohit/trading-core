"""
Phase 7E — Backtesting Engine

Replays historical klines from Binance fapi to test strategies.
Runs all 4 seed strategies through historical data.

Usage:
    python backtest.py --symbol BTCUSDT --start 2025-01-01 --end 2025-04-01
    python backtest.py --symbol BTCUSDT,ETHUSDT --start 2025-01-01 --end 2025-04-01 --interval 1h
    python backtest.py --symbol BTCUSDT --start 2025-01-01 --end 2025-04-01 --sizing atr --output results.json
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Add project root to path
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from market import _curl_get
from strategies.detectors import detect_all
from strategies.environment import EnvironmentCheck
from market_snapshot import get_market_snapshot
from signals import analyze
from social_heat import get_heat_for_symbol

try:
    from config import (
        ATR_STOP_MULTIPLIER, RISK_PER_TRADE_PCT, TP1_R_MULTIPLE, TP2_R_MULTIPLE,
        TP1_CLOSE_PCT, TP2_CLOSE_PCT, TRAILING_STOP_ATR_MULT, LEVERAGE,
        POSITION_PCT, MAX_OPEN_POSITIONS, MIN_NOTIONAL_USDT,
        ENTRY_QUALITY_MIN_PASSED, ENTRY_QUALITY_MIN_SCORE,
    )
except ImportError:
    pass


# ============================================================
# Data layer — fetch historical klines
# ============================================================

def fetch_klines(symbol: str, interval: str = "15m",
                 start: str = None, end: str = None,
                 limit: int = 1000) -> list[dict]:
    """
    Fetch historical klines from Binance fapi.
    Returns list of {open_time, open, high, low, close, volume, quote_volume}.
    """
    all_klines = []
    start_ms = None
    end_ms = None

    if start:
        start_ms = int(datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    if end:
        end_ms = int(datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)

    params = {"symbol": symbol, "interval": interval, "limit": min(limit, 1000)}
    if start_ms:
        params["startTime"] = start_ms
    if end_ms:
        params["endTime"] = end_ms

    url = "https://fapi.binance.com/fapi/v1/klines"
    while True:
        data = _curl_get(url + "?" + "&".join(f"{k}={v}" for k, v in params.items()))
        if not data or not isinstance(data, list):
            break

        for row in data:
            if isinstance(row, list) and len(row) >= 11:
                all_klines.append({
                    "open_time": row[0],
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[5]),
                    "close_time": row[6],
                    "quote_volume": float(row[7]),
                    "trades": int(row[8]),
                })

        if len(data) < 1000:
            break

        # Next batch
        params["startTime"] = data[-1][6] + 1

    return all_klines


# ============================================================
# Historical snapshot builder
# ============================================================

def build_historical_snapshot(symbol: str, klines: list[dict], idx: int,
                              window: int = 14) -> dict:
    """
    Build a simplified market snapshot from historical klines.
    Uses price data from klines; sets OI/taker/depth to neutral defaults.
    """
    if idx < window:
        return None  # Not enough data for ATR

    recent = klines[idx - window:idx]
    current = klines[idx]

    price = current["close"]
    open_price = current["open"]
    high = current["high"]
    low = current["low"]

    # ATR from klines
    tr_values = []
    for i in range(1, len(recent)):
        prev_close = recent[i - 1]["close"]
        tr = max(recent[i]["high"] - recent[i]["low"],
                 abs(recent[i]["high"] - prev_close),
                 abs(recent[i]["low"] - prev_close))
        tr_values.append(tr)
    atr = sum(tr_values) / len(tr_values) if tr_values else price * 0.005
    atr_pct = (atr / price) * 100

    # Price changes
    if len(klines) > 1:
        prev = klines[idx - 1]
        change_15m = ((price - prev["close"]) / prev["close"]) * 100
    else:
        change_15m = 0

    if len(klines) > 4:
        prev_4h = klines[idx - 4]
        change_1h = ((price - prev_4h["close"]) / prev_4h["close"]) * 100
    else:
        change_1h = 0

    if len(klines) > 16:
        prev_16h = klines[idx - 16]
        change_4h = ((price - prev_16h["close"]) / prev_16h["close"]) * 100
    else:
        change_4h = 0

    if len(klines) > 96:
        prev_96 = klines[idx - 96]
        change_24h = ((price - prev_96["close"]) / prev_96["close"]) * 100
    else:
        change_24h = 0

    return {
        "symbol": symbol,
        "price": price,
        "open": open_price,
        "high": high,
        "low": low,
        "volume": current["volume"],
        "quote_volume": current["quote_volume"],
        "atr": atr,
        "atr_pct": round(atr_pct, 4),
        "change_15m": round(change_15m, 4),
        "change_1h": round(change_1h, 4),
        "change_4h": round(change_4h, 4),
        "change_24h": round(change_24h, 4),
        # Neutral defaults for unavailable historical data
        "oi": 0,
        "oi_15m_change": 0,
        "oi_1h_change": 0,
        "oi_4h_change": 0,
        "funding_rate": 0.01,
        "global_lsr": 1.0,
        "top_lsr": 1.0,
        "taker_ratio": 1.0,
        "taker_trend_pct": 0,
        "depth_imbalance": 0,
    }


# ============================================================
# Backtest engine
# ============================================================

class BacktestEngine:
    """
    Replay historical data and simulate trading.
    """

    def __init__(self, symbols: list[str], klines_by_symbol: dict,
                 initial_balance: float = 10000.0,
                 leverage: int = 3,
                 sizing_mode: str = "atr",
                 atr_multiplier: float = 1.5,
                 risk_pct: float = 2.0,
                 tp1_r: float = 1.5,
                 tp2_r: float = 3.0,
                 tp1_close_pct: float = 30,
                 tp2_close_pct: float = 30,
                 trail_atr_mult: float = 2.0,
                 min_passed: int = 4,
                 min_score: int = 50,
                 cooldown_candles: int = 0,
                 max_positions: int = 3,
                 # Phase 9A: Realism Parameters
                 taker_fee_rate: float = 0.0004,  # 0.04% Binance Taker
                 slippage_pct: float = 0.0001):   # 1 tick approx
        self.symbols = symbols
        self.klines_by_symbol = klines_by_symbol
        self.initial_balance = initial_balance
        self.leverage = leverage
        self.sizing_mode = sizing_mode
        self.atr_multiplier = atr_multiplier
        self.risk_pct = risk_pct
        self.tp1_r = tp1_r
        self.tp2_r = tp2_r
        self.tp1_close_pct = tp1_close_pct
        self.tp2_close_pct = tp2_close_pct
        self.trail_atr_mult = trail_atr_mult
        self.min_passed = min_passed
        self.min_score = min_score
        self.cooldown_candles = cooldown_candles
        self.max_positions = max_positions
        
        # Phase 9A
        self.taker_fee_rate = taker_fee_rate
        self.slippage_pct = slippage_pct

        # State
        self.balance = initial_balance
        self.equity = initial_balance
        self.positions: dict[str, dict] = {}  # symbol -> position
        self.trades: list[dict] = []
        self.equity_curve: list[dict] = []
        self.candle_idx = 0
        self.cooldown_until: dict[str, int] = {}  # symbol -> candle idx
        self.total_fees_paid = 0.0

        # Stats
        self.total_pnl = 0.0
        self.max_equity = initial_balance
        self.max_drawdown = 0.0

    def run(self) -> dict:
        """Run backtest across all symbols."""
        # Find common length
        min_len = min(len(klines) for klines in self.klines_by_symbol.values())
        if min_len < 20:
            return {"error": "Not enough kline data"}

        print(f"Running backtest on {len(self.symbols)} symbols, {min_len} candles each...")

        for idx in range(14, min_len):
            self.candle_idx = idx

            # Close positions that hit TP/SL
            self._check_positions(idx)

            # Try to open new positions
            for symbol in self.symbols:
                if symbol in self.cooldown_until and idx < self.cooldown_until[symbol]:
                    continue
                if symbol in self.positions:
                    continue
                if len(self.positions) >= self.max_positions:
                    break

                self._try_open(symbol, idx)

            # Record equity
            self.equity = self.balance + sum(
                self._unrealized_pnl(pos, self.klines_by_symbol[pos["symbol"]][idx]["close"])
                for pos in self.positions.values()
            )
            self.max_equity = max(self.max_equity, self.equity)
            drawdown = (self.max_equity - self.equity) / self.max_equity * 100
            self.max_drawdown = max(self.max_drawdown, drawdown)

            if idx % 100 == 0:
                self.equity_curve.append({
                    "candle": idx,
                    "equity": round(self.equity, 2),
                    "balance": round(self.balance, 2),
                    "open_positions": len(self.positions),
                })

        # Close all remaining positions at last candle
        last_idx = min_len - 1
        for symbol in list(self.positions.keys()):
            self._force_close(symbol, self.klines_by_symbol[symbol][last_idx]["close"],
                              "end_of_backtest", last_idx)

        return self._summary()

    def _try_open(self, symbol: str, idx: int):
        """Try to open a position for symbol at candle idx."""
        klines = self.klines_by_symbol[symbol]
        snapshot = build_historical_snapshot(symbol, klines, idx)
        if not snapshot:
            return

        price = snapshot["price"]
        open_price = snapshot["open"]

        # Simplified backtest signal generation:
        # Detect momentum patterns from kline data directly
        signal = self._detect_backtest_signal(symbol, snapshot, klines, idx)
        if not signal:
            return

        best = signal

        # Signal analysis from snapshot
        signal_analysis = analyze(snapshot)
        score = signal_analysis.get("score", 0)

        # Hard veto
        if abs(snapshot.get("change_4h", 0)) > 25:
            return
        if abs(snapshot.get("change_24h", 0)) > 50:
            return

        # Quality check
        if score < self.min_score:
            return

        # Determine entry price and SL/TP
        entry_price = price * (1 + self.slippage_pct) # Apply slippage
        atr = snapshot["atr"]

        if self.sizing_mode == "atr":
            stop_distance = atr * self.atr_multiplier
            sl_pct = stop_distance / entry_price
        else:
            sl_pct = 0.05  # Default fixed stop

        if best["direction"] == "long":
            stop_loss = entry_price * (1 - sl_pct)
            tp1_price = entry_price + (entry_price - stop_loss) * self.tp1_r
            tp2_price = entry_price + (entry_price - stop_loss) * self.tp2_r
        else:
            entry_price = price * (1 - self.slippage_pct) # Apply slippage for short
            stop_loss = entry_price * (1 + sl_pct)
            tp1_price = entry_price - (stop_loss - entry_price) * self.tp1_r
            tp2_price = entry_price - (stop_loss - entry_price) * self.tp2_r

        # Position sizing
        risk_amount = self.equity * self.risk_pct / 100
        if self.sizing_mode == "atr":
            notional = risk_amount / sl_pct if sl_pct > 0 else risk_amount / 0.02
        else:
            notional = self.equity * self.risk_pct * self.leverage / 100

        position_usd = notional / self.leverage
        
        # Deduct Entry Fee
        entry_fee = position_usd * self.leverage * self.taker_fee_rate
        self.balance -= entry_fee
        self.total_fees_paid += entry_fee

        self.positions[symbol] = {
            "symbol": symbol,
            "direction": best["direction"],
            "entry_price": entry_price,
            "entry_idx": idx,
            "stop_loss": stop_loss,
            "tp1_price": tp1_price,
            "tp2_price": tp2_price,
            "position_usd": position_usd,
            "remaining_pct": 100,
            "tp1_done": False,
            "tp2_done": False,
            "peak_price": entry_price,
            "signal_type": best["type"],
            "initial_r": entry_price - stop_loss if best["direction"] == "long" else stop_loss - entry_price,
        }

    def _detect_backtest_signal(self, symbol: str, snapshot: dict,
                                 klines: list[dict], idx: int) -> dict | None:
        """
        Simplified signal detection for backtesting using kline data only.
        Detects momentum patterns without needing live Market API calls.
        """
        change_24h = snapshot.get("change_24h", 0)
        change_15m = snapshot.get("change_15m", 0)
        change_1h = snapshot.get("change_1h", 0)

        # Strategy 1: Crash bounce long (price dropped > 2% then recovering)
        if change_24h < -2 and change_15m > 0:
            return {
                "type": "crash_bounce_long",
                "direction": "long",
                "strength": "A" if change_24h < -5 else "B",
            }

        # Strategy 2: Pump short (price pumped > 5%)
        if change_24h > 5 and change_15m < 0:
            return {
                "type": "pump_short",
                "direction": "short",
                "strength": "A" if change_24h > 10 else "B",
            }

        # Strategy 3: Momentum long (steady uptrend)
        if change_1h > 1 and change_15m > 0.5:
            return {
                "type": "neg_funding_long",
                "direction": "long",
                "strength": "A" if change_1h > 3 else "B",
            }

        # Strategy 4: Mean reversion short (overextended up)
        if change_1h > 3 and change_15m < -0.5:
            return {
                "type": "pos_funding_short",
                "direction": "short",
                "strength": "A" if change_1h > 5 else "B",
            }

        return None

    def _check_positions(self, idx: int):
        """Check all open positions for TP/SL triggers."""
        for symbol in list(self.positions.keys()):
            pos = self.positions[symbol]
            klines = self.klines_by_symbol[symbol]
            candle = klines[idx]

            price = candle["close"]
            high = candle["high"]
            low = candle["low"]

            # Update peak
            if pos["direction"] == "long":
                pos["peak_price"] = max(pos["peak_price"], high)
            else:
                pos["peak_price"] = min(pos["peak_price"], low)

            # Check SL
            if pos["direction"] == "long" and low <= pos["stop_loss"]:
                self._force_close(symbol, pos["stop_loss"], "sl", idx)
                continue
            elif pos["direction"] == "short" and high >= pos["stop_loss"]:
                self._force_close(symbol, pos["stop_loss"], "sl", idx)
                continue

            # Check TP1
            if not pos["tp1_done"]:
                if pos["direction"] == "long" and high >= pos["tp1_price"]:
                    self._partial_close(symbol, price, "tp1", idx)
                    continue
                elif pos["direction"] == "short" and low <= pos["tp1_price"]:
                    self._partial_close(symbol, price, "tp1", idx)
                    continue

            # Check TP2
            if pos["tp1_done"] and not pos["tp2_done"]:
                if pos["direction"] == "long" and high >= pos["tp2_price"]:
                    self._partial_close(symbol, price, "tp2", idx)
                    continue
                elif pos["direction"] == "short" and low <= pos["tp2_price"]:
                    self._partial_close(symbol, price, "tp2", idx)
                    continue

            # Trailing stop (after TP2)
            if pos["tp1_done"] and pos["tp2_done"] and pos["remaining_pct"] > 0:
                atr = klines[idx]["high"] - klines[idx]["low"]  # Simplified
                if pos["direction"] == "long":
                    trail = pos["peak_price"] - atr * self.trail_atr_mult
                    if low <= trail:
                        self._force_close(symbol, trail, "trailing", idx)
                else:
                    trail = pos["peak_price"] + atr * self.trail_atr_mult
                    if high >= trail:
                        self._force_close(symbol, trail, "trailing", idx)

    def _partial_close(self, symbol: str, exit_price: float, reason: str, idx: int):
        pos = self.positions[symbol]
        remaining = pos["remaining_pct"]

        if reason == "tp1":
            close_pct = self.tp1_close_pct
            pos["tp1_done"] = True
            # Move SL to breakeven
            pos["stop_loss"] = pos["entry_price"]
        elif reason == "tp2":
            close_pct = self.tp2_close_pct
            pos["tp2_done"] = True

        pnl_pct = self._calc_pnl_pct(pos, exit_price)
        
        # Apply exit slippage
        if pos["direction"] == "long":
            exit_price *= (1 - self.slippage_pct)
        else:
            exit_price *= (1 + self.slippage_pct)
            
        pnl_usd = pos["position_usd"] * (close_pct / 100) * pnl_pct / 100 * self.leverage
        
        # Deduct Exit Fee
        exit_fee = pos["position_usd"] * (close_pct / 100) * self.leverage * self.taker_fee_rate
        pnl_usd -= exit_fee
        self.total_fees_paid += exit_fee

        pos["remaining_pct"] = max(remaining - close_pct, 0)
        self.balance += pnl_usd
        self.total_pnl += pnl_usd

        self.trades.append({
            "symbol": symbol,
            "direction": pos["direction"],
            "signal_type": pos["signal_type"],
            "entry_price": pos["entry_price"],
            "exit_price": exit_price,
            "exit_idx": idx,
            "entry_idx": pos["entry_idx"],
            "pnl_pct": round(pnl_pct * close_pct / 100, 4),
            "pnl_usd": round(pnl_usd, 4),
            "exit_reason": reason,
            "partial": True,
            "remaining_pct": pos["remaining_pct"],
        })

    def _force_close(self, symbol: str, exit_price: float, reason: str, idx: int):
        pos = self.positions[symbol]
        pnl_pct = self._calc_pnl_pct(pos, exit_price)
        
        # Apply exit slippage
        if pos["direction"] == "long":
            exit_price *= (1 - self.slippage_pct)
        else:
            exit_price *= (1 + self.slippage_pct)
            
        pnl_usd = pos["position_usd"] * (pos["remaining_pct"] / 100) * pnl_pct / 100 * self.leverage
        
        # Deduct Exit Fee
        exit_fee = pos["position_usd"] * (pos["remaining_pct"] / 100) * self.leverage * self.taker_fee_rate
        pnl_usd -= exit_fee
        self.total_fees_paid += exit_fee

        self.balance += pnl_usd
        self.total_pnl += pnl_usd

        # Cooldown
        self.cooldown_until[symbol] = idx + self.cooldown_candles

        self.trades.append({
            "symbol": symbol,
            "direction": pos["direction"],
            "signal_type": pos["signal_type"],
            "entry_price": pos["entry_price"],
            "exit_price": exit_price,
            "exit_idx": idx,
            "entry_idx": pos["entry_idx"],
            "pnl_pct": round(pnl_pct * pos["remaining_pct"] / 100, 4),
            "pnl_usd": round(pnl_usd, 4),
            "exit_reason": reason,
            "partial": False,
            "remaining_pct": 0,
        })

        del self.positions[symbol]

    def _calc_pnl_pct(self, pos: dict, exit_price: float) -> float:
        entry = pos["entry_price"]
        if pos["direction"] == "long":
            return (exit_price - entry) / entry * 100
        return (entry - exit_price) / entry * 100

    def _unrealized_pnl(self, pos: dict, current_price: float) -> float:
        pnl_pct = self._calc_pnl_pct(pos, current_price)
        return pos["position_usd"] * (pos["remaining_pct"] / 100) * pnl_pct / 100 * self.leverage

    def _summary(self) -> dict:
        total_trades = len(self.trades)
        winning = [t for t in self.trades if t["pnl_usd"] > 0]
        losing = [t for t in self.trades if t["pnl_usd"] <= 0]

        win_rate = len(winning) / total_trades * 100 if total_trades > 0 else 0
        avg_pnl = sum(t["pnl_pct"] for t in self.trades) / total_trades if total_trades > 0 else 0
        total_pnl_pct = (self.balance - self.initial_balance) / self.initial_balance * 100

        # Per-strategy stats
        strategy_stats = {}
        for t in self.trades:
            sig = t["signal_type"]
            if sig not in strategy_stats:
                strategy_stats[sig] = {"trades": 0, "wins": 0, "total_pnl": 0, "avg_pnl": 0}
            strategy_stats[sig]["trades"] += 1
            if t["pnl_usd"] > 0:
                strategy_stats[sig]["wins"] += 1
            strategy_stats[sig]["total_pnl"] += t["pnl_usd"]

        for sig in strategy_stats:
            s = strategy_stats[sig]
            s["win_rate"] = round(s["wins"] / s["trades"] * 100, 1) if s["trades"] > 0 else 0
            s["avg_pnl"] = round(s["total_pnl"] / s["trades"], 2) if s["trades"] > 0 else 0

        # Per-symbol stats
        symbol_stats = {}
        for t in self.trades:
            sym = t["symbol"]
            if sym not in symbol_stats:
                symbol_stats[sym] = {"trades": 0, "wins": 0, "total_pnl": 0}
            symbol_stats[sym]["trades"] += 1
            if t["pnl_usd"] > 0:
                symbol_stats[sym]["wins"] += 1
            symbol_stats[sym]["total_pnl"] += t["pnl_usd"]

        for sym in symbol_stats:
            s = symbol_stats[sym]
            s["win_rate"] = round(s["wins"] / s["trades"] * 100, 1) if s["trades"] > 0 else 0

        return {
            "initial_balance": self.initial_balance,
            "final_balance": round(self.balance, 2),
            "total_pnl_usd": round(self.total_pnl, 2),
            "total_pnl_pct": round(total_pnl_pct, 2),
            "total_fees_paid": round(self.total_fees_paid, 2),
            "total_trades": total_trades,
            "winning_trades": len(winning),
            "losing_trades": len(losing),
            "win_rate": round(win_rate, 1),
            "avg_pnl_pct": round(avg_pnl, 4),
            "max_drawdown_pct": round(self.max_drawdown, 2),
            "max_equity": round(self.max_equity, 2),
            "final_equity": round(self.equity, 2),
            "strategy_stats": strategy_stats,
            "symbol_stats": symbol_stats,
            "sizing_mode": self.sizing_mode,
            "leverage": self.leverage,
            "atr_multiplier": self.atr_multiplier,
            "risk_pct": self.risk_pct,
            "equity_curve": self.equity_curve,
            "trades": self.trades[:100],  # First 100 trades
        }


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Trading Core Backtest Engine")
    parser.add_argument("--symbol", required=True, help="Symbol(s), comma-separated")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--interval", default="15m", help="Kline interval (1m/5m/15m/1h/4h)")
    parser.add_argument("--initial-balance", type=float, default=10000.0)
    parser.add_argument("--leverage", type=int, default=3)
    parser.add_argument("--sizing", default="atr", choices=["atr", "fixed"])
    parser.add_argument("--atr-multiplier", type=float, default=1.5)
    parser.add_argument("--risk-pct", type=float, default=2.0)
    parser.add_argument("--output", help="Output JSON file")
    parser.add_argument("--quiet", action="store_true", help="Suppress verbose output")

    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbol.split(",")]

    # Fetch klines
    klines_by_symbol = {}
    for symbol in symbols:
        if not args.quiet:
            print(f"Fetching klines for {symbol} ({args.interval})...")
        klines = fetch_klines(symbol, args.interval, args.start, args.end)
        if not klines:
            print(f"Warning: No klines for {symbol}")
            continue
        klines_by_symbol[symbol] = klines
        if not args.quiet:
            print(f"  Got {len(klines)} klines for {symbol}")

    if not klines_by_symbol:
        print("Error: No klines fetched for any symbol")
        sys.exit(1)

    # Run backtest
    engine = BacktestEngine(
        symbols=list(klines_by_symbol.keys()),
        klines_by_symbol=klines_by_symbol,
        initial_balance=args.initial_balance,
        leverage=args.leverage,
        sizing_mode=args.sizing,
        atr_multiplier=args.atr_multiplier,
        risk_pct=args.risk_pct,
        cooldown_candles=4 if args.interval == "15m" else 2,
    )

    result = engine.run()

    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)

    # Print summary
    print(f"\n{'='*50}")
    print(f"Backtest Results")
    print(f"{'='*50}")
    print(f"Symbols:         {', '.join(symbols)}")
    print(f"Period:          {args.start} to {args.end}")
    print(f"Interval:        {args.interval}")
    print(f"Sizing:          {args.sizing} (ATR mult={args.atr_multiplier})")
    print(f"Leverage:        {args.leverage}x")
    print(f"Risk per trade:  {args.risk_pct}%")
    print(f"{'='*50}")
    print(f"Initial balance: ${result['initial_balance']:,.2f}")
    print(f"Final balance:   ${result['final_balance']:,.2f}")
    print(f"Total PnL:       ${result['total_pnl_usd']:,.2f} ({result['total_pnl_pct']:+.2f}%)")
    print(f"Total trades:    {result['total_trades']}")
    print(f"Win rate:        {result['win_rate']:.1f}%")
    print(f"Avg PnL/trade:   {result['avg_pnl_pct']:+.4f}%")
    print(f"Max drawdown:    {result['max_drawdown_pct']:.2f}%")
    print(f"{'='*50}")

    if result["strategy_stats"]:
        print(f"\nPer-Strategy Stats:")
        print(f"{'Strategy':<25} {'Trades':>6} {'Win Rate':>8} {'Total PnL':>10}")
        print(f"{'-'*50}")
        for sig, stats in sorted(result["strategy_stats"].items(),
                                  key=lambda x: -x[1]["total_pnl"]):
            print(f"{sig:<25} {stats['trades']:>6} {stats['win_rate']:>7.1f}% ${stats['total_pnl']:>9,.2f}")

    if result["symbol_stats"]:
        print(f"\nPer-Symbol Stats:")
        print(f"{'Symbol':<15} {'Trades':>6} {'Win Rate':>8} {'Total PnL':>10}")
        print(f"{'-'*40}")
        for sym, stats in sorted(result["symbol_stats"].items(),
                                  key=lambda x: -x[1]["total_pnl"]):
            print(f"{sym:<15} {stats['trades']:>6} {stats['win_rate']:>7.1f}% ${stats['total_pnl']:>9,.2f}")

    # Save output
    if args.output:
        output_path = Path(args.output)
        # Remove equity curve for human-readable output
        compact_result = {k: v for k, v in result.items() if k != "equity_curve"}
        compact_result["equity_curve"] = result["equity_curve"][-20:]  # Last 20 points
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(compact_result, f, indent=2)
        print(f"\nResults saved to {output_path}")

    return result


if __name__ == "__main__":
    main()
