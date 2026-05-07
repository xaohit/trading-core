#!/usr/bin/env python3
"""
entry_decision.py — 15min 决策报告 & 8h 复盘
用法:
  python3 entry_decision.py decision  — 扫描 + 决策报告
  python3 entry_decision.py reflection — 8h 复盘报告
"""
import sys, os, asyncio, json, subprocess
from datetime import datetime

os.environ["http_proxy"] = "http://localhost:7897"
os.environ["https_proxy"] = "http://localhost:7897"

sys.path.insert(0, "/tmp/trading_core")
from monitor.market_monitor import MarketMonitor


def _nanobot_push(content: str) -> bool:
    PY = "/usr/local/bin/python3"
    cmd = [PY, "-m", "nanobot", "agent", "-m", f"微信推送：\n\n{content}"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd="/Users/xaohit")
        return r.returncode == 0
    except Exception as e:
        print(f"[push] failed: {e}", file=sys.stderr)
        return False


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
    return await m.run_once()


def build_decision_report(result):
    signals = result.get("signals", [])
    count = result["signals_found"]
    now_str = datetime.now().strftime("%H:%M")

    report = [f"📡 Herms 决策 | {now_str} | +{count} 信号\n"]

    if count == 0:
        report.append("当前无高强度信号，观望")
        report.append("\n⚠️ 仅作参考，不构成投资建议")
        return "\n".join(report)

    top = signals[0]
    report.append(f"🏆 Top: {top['symbol']} | {top['direction'].upper()} | 强度 {top['strength']:.1f}")
    report.append(f"   {top['reason']}")
    report.append(f"   价格 ${top['price']:,.4f} | 24h {top['change_24h']:+.1f}%")

    if count > 1:
        report.append(f"\n📋 次级候选 ({count - 1} 个):")
        for s in signals[1:5]:
            emoji = "📈" if s["direction"] == "long" else "📉"
            report.append(f"{emoji} {s['symbol']} {s['direction'].upper()} str={s['strength']:.1f} | {s['reason']}")

    report.append("\n⚠️ 仅作参考，不构成投资建议")
    return "\n".join(report)


def build_reflection_report():
    signals = _load_signals()
    now_str = datetime.now().strftime("%H:%M")

    if not signals:
        return f"📊 Herms 8h 复盘 | {now_str}\n\n无信号记录\n\n⚠️ 仅作参考，不构成投资建议"

    strong = [s for s in signals if s.get("strength", 0) >= 6]
    medium = [s for s in signals if 4 <= s.get("strength", 0) < 6]
    longs = [s for s in signals if s.get("direction") == "long"]
    shorts = [s for s in signals if s.get("direction") == "short"]

    report = [f"📊 Herms 8h 复盘 | {now_str}"]
    report.append(f"信号总数: {len(signals)} | 做多: {len(longs)} | 做空: {len(shorts)}")
    report.append(f"强信号 (≥6分): {len(strong)} | 中信号 (4-6分): {len(medium)}\n")

    if strong:
        report.append("🔥 强信号:")
        for s in sorted(strong, key=lambda x: -x["strength"])[:5]:
            emoji = "📈" if s["direction"] == "long" else "📉"
            report.append(f"  {emoji} {s['symbol']} {s['direction'].upper()} str={s['strength']:.1f} | {s['reason']}")

    if medium:
        report.append("⚡ 中信号:")
        for s in sorted(medium, key=lambda x: -x["strength"])[:5]:
            emoji = "📈" if s["direction"] == "long" else "📉"
            report.append(f"  {emoji} {s['symbol']} {s['direction'].upper()} str={s['strength']:.1f}")

    report.append("\n⚠️ 仅作参考，不构成投资建议")
    return "\n".join(report)


async def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "decision"

    if mode == "reflection":
        report = build_reflection_report()
        print(report, file=sys.stderr)
        _nanobot_push(report)
        return

    # Decision mode
    result = await run_scan()
    report = build_decision_report(result)
    print(report, file=sys.stderr)
    _nanobot_push(report)


if __name__ == "__main__":
    asyncio.run(main())