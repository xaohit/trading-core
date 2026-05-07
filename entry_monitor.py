#!/usr/bin/env python3
"""entry_monitor.py — 信号扫描，存文件，不推微信"""
import sys, os, asyncio, json
from datetime import datetime

os.environ["http_proxy"] = "http://localhost:7897"
os.environ["https_proxy"] = "http://localhost:7897"

sys.path.insert(0, "/tmp/trading_core")
from monitor.market_monitor import MarketMonitor


def _save_signals(signals):
    state_dir = os.path.expanduser("~/.hermes")
    os.makedirs(state_dir, exist_ok=True)
    path = os.path.join(state_dir, "recent_signals.json")
    try:
        with open(path, "w") as f:
            json.dump(signals, f)
    except Exception:
        pass


def _load_signals():
    path = os.path.expanduser("~/.hermes/recent_signals.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return []


async def run_scan():
    m = MarketMonitor()
    result = await m.run_once()
    signals = result.get("signals", [])
    count = result["signals_found"]

    if count > 0:
        existing = _load_signals()
        seen = {s["symbol"]: s for s in existing}
        for s in signals:
            seen[s["symbol"]] = s
        _save_signals(list(seen.values())[-100:])

    return result


if __name__ == "__main__":
    asyncio.run(run_scan())