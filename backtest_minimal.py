#!/usr/bin/env python3
"""Minimal backtest: run detectors on BTCUSDT 30 days, 1h candles."""
import sys, json, time, requests
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, "/Users/xaohit/trading-core")

PROXY = "socks5h://localhost:7897"
proxies = {"https": PROXY, "http": PROXY}
base_url = "https://fapi.binance.com"

end_time = int(time.time() * 1000)
start_time = int((time.time() - 30 * 24 * 3600) * 1000)

# Fetch 1h klines
klines_1h = []
batch_start = start_time
while batch_start < end_time:
    params = {
        "symbol": "BTCUSDT",
        "interval": "1h",
        "startTime": batch_start,
        "endTime": min(batch_start + 1000 * 60 * 60 * 1000, end_time),
        "limit": 1000,
    }
    try:
        resp = requests.get(base_url + "/fapi/v1/klines", params=params, proxies=proxies, timeout=30)
        batch = resp.json()
        if not batch or isinstance(batch, dict):
            break
        klines_1h.extend(batch)
        batch_start = batch[-1][0] + 1
        if len(batch) < 1000:
            break
    except Exception as e:
        print(f"Fetch error: {e}")
        break

print(f"Fetched {len(klines_1h)} 1h candles")
first = datetime.fromtimestamp(klines_1h[0][0] / 1000)
last = datetime.fromtimestamp(klines_1h[-1][0] / 1000)
print(f"Range: {first} → {last}")

# ── Build 24h change for each candle (we don't have real funding data) ──
from strategies.detectors import (
    detect_crash_bounce,
    detect_pump_short,
)

signals = []

for i in range(35, len(klines_1h)):  # need 32+ for MA/ADX
    window_24 = klines_1h[i - 24 : i + 1] if i >= 24 else klines_1h[:i + 1]
    window_32 = klines_1h[i - 32 : i + 1] if i >= 32 else klines_1h[:i + 1]

    current_close = float(klines_1h[i][4])
    open_24h = float(window_24[0][1])
    change_pct = (current_close - open_24h) / open_24h * 100

    ticker = {
        "symbol": "BTCUSDT",
        "lastPrice": str(current_close),
        "priceChangePercent": str(round(change_pct, 2)),
        "highPrice": str(max(float(k[2]) for k in window_24)),
        "lowPrice": str(min(float(k[3]) for k in window_24)),
    }

    try:
        crash = detect_crash_bounce(ticker, window_32)
        if crash:
            crash["timestamp"] = datetime.fromtimestamp(klines_1h[i][0] / 1000)
            crash["entry_price"] = current_close
            signals.append(crash)

        pump = detect_pump_short(ticker, window_32)
        if pump:
            pump["timestamp"] = datetime.fromtimestamp(klines_1h[i][0] / 1000)
            pump["entry_price"] = current_close
            signals.append(pump)
    except Exception as e:
        pass  # skip malformed windows

print(f"\nSignals detected: {len(signals)}")
stype_counts = defaultdict(int)
for s in signals:
    stype_counts[s["type"]] += 1
    stype_counts[s.get("strength", "?") + "-" + s["type"]] += 1
for k, v in sorted(stype_counts.items(), key=lambda x: -x[1]):
    if k.endswith("_long") or k.endswith("_short"):
        print(f"  {k}: {v}")

# ── Simple forward simulation ──
trades = []
position = None

def calc_pnl(pos_type, entry, exit_p, direction):
    if direction == "short":
        return (entry - exit_p) / entry * 100
    return (exit_p - entry) / entry * 100

for i in range(len(klines_1h)):
    # Close any open position after its hold period
    if position:
        bars_held = i - position["entry_idx"]
        close_price = float(klines_1h[i][4])
        # Close at next opposite signal, or after 24 bars (24h), or stop loss 5%/ tp 10%
        pnl = calc_pnl(position["type"], position["entry_price"], close_price, position["direction"])

        stop_hit = pnl <= -5.0
        tp_hit = pnl >= 10.0
        time_exit = bars_held >= 24

        # Check opposite signal
        window_32 = klines_1h[max(0, i - 32) : i + 1]
        ticker_i = {
            "symbol": "BTCUSDT",
            "lastPrice": str(close_price),
            "priceChangePercent": str((close_price - float(klines_1h[max(0, i - 24)][1])) / float(klines_1h[max(0, i - 24)][1]) * 100),
        }
        has_opposite = False
        try:
            if position["direction"] == "long":
                has_opposite = detect_pump_short(ticker_i, window_32) is not None
            else:
                has_opposite = detect_crash_bounce(ticker_i, window_32) is not None
        except:
            pass

        if stop_hit or tp_hit or time_exit or has_opposite:
            reason = "sl" if stop_hit else "tp" if tp_hit else "time" if time_exit else "signal_flip"
            trades.append({
                "signal": position["type"],
                "direction": position["direction"],
                "entry_ts": position["entry_ts"],
                "exit_ts": datetime.fromtimestamp(klines_1h[i][0] / 1000),
                "entry_price": position["entry_price"],
                "exit_price": close_price,
                "pnl_pct": pnl,
                "win": pnl > 0,
                "bars": bars_held,
                "reason": reason,
            })
            position = None

    # Open new position at signal
    current_signals = [s for s in signals if s.get("entry_idx") == i]
    if not position and current_signals:
        sig = current_signals[0]
        position = {
            "type": sig["type"],
            "direction": sig["direction"],
            "entry_price": sig["entry_price"],
            "entry_ts": sig["timestamp"],
            "entry_idx": i,
        }

# Close final position
if position:
    final_price = float(klines_1h[-1][4])
    pnl = calc_pnl(position["type"], position["entry_price"], final_price, position["direction"])
    trades.append({
        "signal": position["type"],
        "direction": position["direction"],
        "entry_ts": position["entry_ts"],
        "exit_ts": last,
        "entry_price": position["entry_price"],
        "exit_price": final_price,
        "pnl_pct": pnl,
        "win": pnl > 0,
        "bars": len(klines_1h) - position["entry_idx"],
        "reason": "end",
    })

# ── Results ──
print(f"\nSimulated trades: {len(trades)}")
wins = [t for t in trades if t["win"]]
losses = [t for t in trades if not t["win"]]
wr = len(wins) / len(trades) * 100 if trades else 0
avg_w = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0
avg_l = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0
total = sum(t["pnl_pct"] for t in trades)
print(f"Win rate: {wr:.1f}%  Avg win: +{avg_w:.2f}%  Avg loss: {avg_l:.2f}%  Total: {total:+.2f}%")

by_sig = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": []})
for t in trades:
    by_sig[t["signal"]]["wins"] += 1 if t["win"] else 0
    by_sig[t["signal"]]["losses"] += 0 if t["win"] else 1
    by_sig[t["signal"]]["pnl"].append(t["pnl_pct"])

print()
for sig, s in sorted(by_sig.items()):
    n = s["wins"] + s["losses"]
    wr2 = s["wins"] / n * 100 if n else 0
    avg2 = sum(s["pnl"]) / n
    print(f"  {sig}: {n}t  WR={wr2:.0f}%  avg={avg2:+.2f}%")

# Recent sample trades
print(f"\nLast 5 trades:")
for t in trades[-5:]:
    print(f"  {t['signal']} {t['direction']}: {t['pnl_pct']:+.2f}% ({t['bars']}bars, {t['reason']})")
