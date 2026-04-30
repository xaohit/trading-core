#!/usr/bin/env python3
"""
Trading Core — Daemon Entry Point
"""
import sys
import time
import signal
import argparse
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR.parent))
sys.path.insert(0, str(BASE_DIR))

try:
    from trading_core.db.connection import init_db
    from trading_core.scanner import Scanner
    from trading_core.realtime_monitor import RealtimeMonitor
except ModuleNotFoundError:
    from db.connection import init_db
    from scanner import Scanner
    from realtime_monitor import RealtimeMonitor

SCAN_INTERVAL = 300  # 5分钟全量扫描一次


def run_once():
    scanner = Scanner()
    result = scanner.run()
    ts = result.get("timestamp", "")

    closed = result.get("closed", [])
    opened = result.get("scan", {}).get("opened", 0)
    action = result.get("scan", {}).get("action", "unknown")

    if closed:
        print(f"[{ts}] 平仓: {[c['symbol'] for c in closed]}")
    if opened:
        s = result.get("scan", {}).get("signal", {})
        print(f"[{ts}] 开仓: {s.get('symbol')} {s.get('direction')} @ {s.get('price')}")

    print(f"[{ts}] {action} | closed={len(closed)} opened={opened}")
    return result


def daemon():
    print(f"[DAEMON] 启动 | 扫描间隔={SCAN_INTERVAL}s")
    monitor = RealtimeMonitor(interval=1.0)
    monitor.start()

    run_once()  # 启动后立即扫一次

    try:
        while True:
            time.sleep(SCAN_INTERVAL)
            run_once()
    except KeyboardInterrupt:
        print("\n[DAEMON] 停止...")
    finally:
        monitor.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="运行一次")
    parser.add_argument("--scan-only", action="store_true", help="只扫描不安防实时层")
    args = parser.parse_args()

    init_db()

    if args.once or args.scan_only:
        run_once()
    else:
        daemon()
