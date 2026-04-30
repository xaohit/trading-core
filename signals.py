"""
Signals Scoring Layer — ZAIJIN88-inspired multi-dimensional verdict.

Input: market_snapshot.get_market_snapshot(symbol) dict + optional social heat score.
Output: score/verdict/tags/notes/oi_divergence.

No trading side effects. Safe to use in scanner, web, tests.
"""
from __future__ import annotations

from typing import Any, Optional


VERDICT_HEALTHY = "✅ 看起来健康"
VERDICT_WATCH = "🎯 值得留意"
VERDICT_OVERHEATED = "⚠ 过热预警"
VERDICT_WEAK = "📉 信号偏弱"
VERDICT_NEUTRAL = "⚪ 中性"
VERDICT_INSUFFICIENT = "数据不足"


def _num(v: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _clamp(v: float, lo: float = 0, hi: float = 100) -> float:
    return max(lo, min(hi, v))


def _add(notes: list, tags: list, score_delta: float, note: str, tag: str | None = None) -> float:
    notes.append(f"{score_delta:+.0f} {note}")
    if tag:
        tags.append(tag)
    return score_delta


def _oi_divergence(snapshot: dict) -> dict:
    price_1h = _num(snapshot.get("change_1h"), 0) or 0
    oi_1h = _num(snapshot.get("oi_1h_change"), 0) or 0

    if price_1h > 2 and oi_1h < -2:
        return {
            "type": "price_up_oi_down",
            "level": "warning",
            "note": "价格上涨但OI下降，可能是空头回补/动能虚弱",
        }
    if price_1h < -2 and oi_1h > 2:
        return {
            "type": "price_down_oi_up",
            "level": "warning",
            "note": "价格下跌但OI上升，可能有新空入场/趋势延续",
        }
    if abs(price_1h) < 1 and oi_1h > 5:
        return {
            "type": "flat_price_oi_up",
            "level": "watch",
            "note": "价格横盘但OI明显上升，可能在蓄势",
        }
    return {"type": "none", "level": "normal", "note": "无明显OI背离"}


def analyze(snapshot: dict, heat_score: float = 0) -> dict:
    """Analyze a market snapshot and return score/verdict/tags/notes."""
    tags: list[str] = []
    notes: list[str] = []
    score = 50.0

    price = _num(snapshot.get("price"), 0) or 0
    if price <= 0:
        return {
            "score": 0,
            "verdict": VERDICT_INSUFFICIENT,
            "tags": ["no_price"],
            "notes": ["价格缺失，无法评分"],
            "oi_divergence": {"type": "unknown", "level": "unknown", "note": "数据不足"},
        }

    # 1. Social heat / candidate heat. Optional for now.
    heat = _num(heat_score, 0) or 0
    if heat >= 80:
        score += _add(notes, tags, 10, "社交/候选热度极高", "hot_social")
    elif heat >= 50:
        score += _add(notes, tags, 5, "社交/候选热度较高", "warm_social")

    # 2. Volume liquidity.
    vol = _num(snapshot.get("quote_volume_24h"), 0) or 0
    if vol >= 100_000_000:
        score += _add(notes, tags, 8, f"24h成交额 {vol/1e6:.0f}M，流动性强", "liquid")
    elif vol >= 20_000_000:
        score += _add(notes, tags, 3, f"24h成交额 {vol/1e6:.0f}M，可交易", "tradable_liquidity")
    else:
        score += _add(notes, tags, -10, f"24h成交额 {vol/1e6:.0f}M，流动性弱", "thin_liquidity")

    # 3. OI attention + OI changes.
    oi = _num(snapshot.get("oi"), 0) or 0
    oi_usd = oi * price
    if oi_usd >= 20_000_000:
        score += _add(notes, tags, 8, f"OI约 {oi_usd/1e6:.1f}M，关注度高", "oi_high")
    elif oi_usd >= 5_000_000:
        score += _add(notes, tags, 4, f"OI约 {oi_usd/1e6:.1f}M，有关注", "oi_ok")
    else:
        score += _add(notes, tags, -6, f"OI约 {oi_usd/1e6:.1f}M，关注度低", "oi_low")

    oi_1h = _num(snapshot.get("oi_1h_change"), 0) or 0
    if oi_1h >= 8:
        score += _add(notes, tags, 10, f"1h OI大增 {oi_1h:.1f}%", "oi_surge")
    elif oi_1h >= 3:
        score += _add(notes, tags, 5, f"1h OI上升 {oi_1h:.1f}%", "oi_rising")
    elif oi_1h <= -8:
        score += _add(notes, tags, -8, f"1h OI大降 {oi_1h:.1f}%", "oi_drop")

    # 4. Taker flow.
    taker = _num(snapshot.get("taker_ratio"), None)
    taker_trend = _num(snapshot.get("taker_trend_pct"), 0) or 0
    if taker is not None:
        if 0.75 <= taker <= 1.45:
            score += _add(notes, tags, 4, f"主动买卖比 {taker:.2f} 正常", "taker_balanced")
        elif taker > 1.8:
            score += _add(notes, tags, -6, f"主动买入过热 {taker:.2f}", "taker_overheated")
        elif taker < 0.55:
            score += _add(notes, tags, -6, f"主动卖出过强 {taker:.2f}", "taker_weak")

    if taker_trend >= 25:
        score += _add(notes, tags, 6, f"买盘趋势增强 {taker_trend:.1f}%", "buy_pressure_rising")
    elif taker_trend <= -25:
        score += _add(notes, tags, -6, f"买盘趋势衰退 {taker_trend:.1f}%", "buy_pressure_falling")

    # 5. LSR crowding.
    global_lsr = _num(snapshot.get("global_lsr"), None)
    top_lsr = _num(snapshot.get("top_lsr"), None)
    if global_lsr is not None:
        if global_lsr > 2.5:
            score += _add(notes, tags, -8, f"全网多空比 {global_lsr:.2f} 多头拥挤", "long_crowded")
        elif global_lsr < 0.45:
            score += _add(notes, tags, -5, f"全网多空比 {global_lsr:.2f} 空头拥挤", "short_crowded")
        else:
            score += _add(notes, tags, 3, f"全网多空比 {global_lsr:.2f} 未拥挤", "lsr_normal")
    if top_lsr is not None and global_lsr is not None:
        if top_lsr > global_lsr * 1.25:
            score += _add(notes, tags, 5, f"大户多头倾向强于全网 ({top_lsr:.2f}>{global_lsr:.2f})", "top_long_bias")
        elif top_lsr < global_lsr * 0.75:
            score += _add(notes, tags, -3, f"大户多头倾向弱于全网 ({top_lsr:.2f}<{global_lsr:.2f})", "top_less_bullish")

    # 6. Depth imbalance.
    depth_imb = _num(snapshot.get("depth_imbalance"), None)
    if depth_imb is not None:
        if depth_imb >= 25:
            score += _add(notes, tags, 5, f"±1%买盘深度占优 {depth_imb:.1f}%", "bid_depth_strong")
        elif depth_imb <= -25:
            score += _add(notes, tags, -5, f"±1%卖盘深度占优 {depth_imb:.1f}%", "ask_depth_strong")
        else:
            score += _add(notes, tags, 2, f"盘口深度均衡 {depth_imb:.1f}%", "depth_balanced")

    # 7. Price momentum / overheat.
    chg_24h = _num(snapshot.get("change_24h"), 0) or 0
    chg_1h = _num(snapshot.get("change_1h"), 0) or 0
    if chg_24h >= 35:
        score += _add(notes, tags, -12, f"24h涨幅 {chg_24h:.1f}% 过热", "price_overheated")
    elif chg_24h <= -20:
        score += _add(notes, tags, -6, f"24h跌幅 {chg_24h:.1f}% 高波动风险", "crash_risk")
    elif 1 <= chg_1h <= 8:
        score += _add(notes, tags, 5, f"1h动量 {chg_1h:.1f}% 温和向上", "momentum_ok")

    # 8. Funding.
    funding = _num(snapshot.get("funding_rate"), None)
    if funding is not None:
        if funding >= 0.12:
            score += _add(notes, tags, -8, f"资金费率 {funding:.3f}% 多头过热", "funding_hot")
        elif funding <= -0.08:
            score += _add(notes, tags, 6, f"资金费率 {funding:.3f}% 偏负，潜在逼空", "funding_negative")
        else:
            score += _add(notes, tags, 2, f"资金费率 {funding:.3f}% 正常", "funding_normal")

    div = _oi_divergence(snapshot)
    if div["level"] == "warning":
        score -= 7
        tags.append(div["type"])
        notes.append(f"-7 {div['note']}")
    elif div["level"] == "watch":
        score += 4
        tags.append(div["type"])
        notes.append(f"+4 {div['note']}")

    score = round(_clamp(score), 1)

    if score >= 72:
        verdict = VERDICT_HEALTHY
    elif score >= 62:
        verdict = VERDICT_WATCH
    elif "price_overheated" in tags or "funding_hot" in tags or "long_crowded" in tags:
        verdict = VERDICT_OVERHEATED
    elif score < 43:
        verdict = VERDICT_WEAK
    else:
        verdict = VERDICT_NEUTRAL

    return {
        "score": score,
        "verdict": verdict,
        "tags": sorted(set(tags)),
        "notes": notes,
        "oi_divergence": div,
    }


if __name__ == "__main__":
    import json
    import sys

    from market_snapshot import get_market_snapshot

    sym = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    snap = get_market_snapshot(sym)
    print(json.dumps(analyze(snap), ensure_ascii=False, indent=2))
