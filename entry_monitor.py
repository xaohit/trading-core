#!/usr/bin/env python3
import sys, os, asyncio, json, subprocess, re
from datetime import datetime

# Proxy for Mac (Clash MITM)
os.environ["http_proxy"] = "http://localhost:7897"
os.environ["https_proxy"] = "http://localhost:7897"

sys.path.insert(0, "/tmp/trading_core")
from monitor.market_monitor import MarketMonitor

WECHAT_ID = "o9cq80y1kkdQ-Z6SR6DqAZuCi370@im.wechat"


def _nanobot_push(content: str) -> bool:
    PY = "/usr/local/bin/python3"
    cmd = [PY, "-m", "nanobot", "agent", "-m", f"微信推送：\n\n{content}"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd="/Users/xaohit")
        print(f"[nanobot push] rc={r.returncode} stdout={r.stdout[:100]} stderr={r.stderr[:100]}", file=sys.stderr)
        return r.returncode == 0
    except Exception as e:
        print(f"[push] failed: {e}", file=sys.stderr)
        return False


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


async def run_scan(push: bool = True):
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

    if not push:
        return result

    if count == 0:
        msg = f"[OK] no signals ({result['tickers_checked']} tickers, {result['duration_s']:.1f}s)"
        print(msg, file=sys.stderr)
        return result

    now_str = datetime.now().strftime("%H:%M")
    lines = [
        f"🔍 Herms Monitor | {now_str}",
        f"扫描 {result['tickers_checked']} 个USDT永续 | 耗时 {result['duration_s']:.1f}s\n",
        f"📡 发现 {count} 个信号:\n",
    ]
    for s in signals:
        emoji = "📈" if s["direction"] == "long" else "📉"
        lines.append(f"{emoji} {s['symbol']} | {s['direction'].upper()} | 强度 {s['strength']:.1f}")
        lines.append(f"   ${s['price']:,.4f} | 24h {s['change_24h']:+.1f}% | {s['reason']}\n")
    lines.append("⚠️ 仅作参考，不构成投资建议")
    content = "\n".join(lines)
    print(content, file=sys.stderr)
    ok = _nanobot_push(content)
    print(f"[push] {'✓' if ok else '✗'}", file=sys.stderr)
    return result


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "scan"
    push = (mode != "check")
    asyncio.run(run_scan(push=push))