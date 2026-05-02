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
    from trading_core.decision_memory import DecisionMemory
except ModuleNotFoundError:
    from db.connection import init_db
    from scanner import Scanner
    from realtime_monitor import RealtimeMonitor
    from decision_memory import DecisionMemory

SCAN_INTERVAL = 60            # FIX #3: 5分钟对meme币太慢，改为1分钟
OPTIMIZE_INTERVAL = 3600     # 1小时调参一次
_last_optimize_ts = 0


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

    import time as _time
    from self_optimizer import run as optimize_run

    try:
        while True:
            _time.sleep(SCAN_INTERVAL)

            # 1. 扫描
            run_once()

            # 2. 复盘到期决策
            reviewed = DecisionMemory.review_due(limit=20)
            if reviewed:
                print(f"[review] 完成 {len(reviewed)} 条复盘")

            # 3. 自动调参（每小时一次）
            global _last_optimize_ts
            if _time.time() - _last_optimize_ts >= OPTIMIZE_INTERVAL:
                _last_optimize_ts = int(_time.time())
                result = optimize_run(dry_run=True)
                if result.get("ok") and result.get("suggestions"):
                    print(f"[optimizer] 发现 {len(result['suggestions'])} 条调参建议（dry-run）")
                elif result.get("ok"):
                    print(f"[optimizer] 阈值正常，无需调整")
                else:
                    print(f"[optimizer] 数据不足，等待积累")

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
