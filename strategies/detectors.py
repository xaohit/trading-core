"""
Strategy Detectors — 4 core strategies
Each detector returns a signal dict or None
"""
from typing import Optional

try:
    from ..config import get_strategy_config, STRENGTH_S, STRENGTH_A
    from ..market import Market
except ImportError:
    from config import get_strategy_config, STRENGTH_S, STRENGTH_A
    from market import Market


def detect_extreme_negative_funding(symbol: str, rate: float,
                                     funding_rates: dict) -> Optional[dict]:
    """资金费率极度为负 → 散户做空被收割，交易所补贴多方 → 逼空"""
    cfg = get_strategy_config("neg_funding_long")
    if rate >= cfg["min_rate"]:
        return None

    klines = Market.klines(symbol, "1h", 24)
    if not klines or len(klines) < 10:
        return None

    closes = [float(k[4]) for k in klines]
    change_pct = (closes[-1] - closes[0]) / closes[0] * 100

    if change_pct > cfg["min_change"]:
        return None

    if rate <= STRENGTH_S["neg_funding"] and change_pct <= -10:
        strength = "S"
    elif rate <= STRENGTH_A["neg_funding"]:
        strength = "A"
    else:
        strength = "B"

    return {
        "type": "neg_funding_long",
        "direction": "long",
        "strength": strength,
        "reason": f"费率{rate:.3f}% 24h跌{change_pct:.1f}% 散户做空被收割",
        "sl_pct": cfg["sl_pct"],
        "tp_pct": cfg["tp_pct"],
        "funding_rate": rate,
        "change_24h": change_pct,
    }


def detect_extreme_positive_funding(symbol: str, rate: float,
                                     funding_rates: dict) -> Optional[dict]:
    """资金费率极度为正 → 散户做多被收割 → 逼空"""
    cfg = get_strategy_config("pos_funding_short")
    if rate <= cfg["min_rate"]:
        return None

    klines = Market.klines(symbol, "1h", 24)
    if not klines or len(klines) < 10:
        return None

    closes = [float(k[4]) for k in klines]
    change_pct = (closes[-1] - closes[0]) / closes[0] * 100

    if change_pct < cfg["min_change"]:
        return None

    if rate >= STRENGTH_S["pos_funding"] and change_pct >= 10:
        strength = "S"
    elif rate >= STRENGTH_A["pos_funding"]:
        strength = "A"
    else:
        strength = "B"

    return {
        "type": "pos_funding_short",
        "direction": "short",
        "strength": strength,
        "reason": f"费率{rate:.3f}% 24h涨{change_pct:.1f}% 散户做多被收割",
        "sl_pct": cfg["sl_pct"],
        "tp_pct": cfg["tp_pct"],
        "funding_rate": rate,
        "change_24h": change_pct,
    }


def detect_crash_bounce(ticker: dict) -> Optional[dict]:
    """24h暴跌后反弹 → 暴力熊市报复性反弹"""
    cfg = get_strategy_config("crash_bounce_long")
    change_pct = float(ticker.get("priceChangePercent", 0))

    if change_pct >= cfg["min_crash"]:
        return None

    klines = Market.klines(ticker["symbol"], "1h", 6)
    if not klines or len(klines) < 3:
        return None

    lows = [float(k[3]) for k in klines]
    current = float(ticker["lastPrice"])
    bottom = min(lows)

    bounce = (current - bottom) / bottom * 100
    if bounce < cfg["min_bounce"]:
        return None

    if change_pct <= -20 and bounce >= 15:
        strength = "S"
    elif change_pct <= -10 and bounce >= 8:
        strength = "A"
    else:
        strength = "B"

    return {
        "type": "crash_bounce_long",
        "direction": "long",
        "strength": strength,
        "reason": f"24h暴跌{change_pct:.1f}%后反弹{bounce:.1f}% 报复性反弹",
        "sl_pct": cfg["sl_pct"],
        "tp_pct": cfg["tp_pct"],
        "change_24h": change_pct,
    }


def detect_pump_short(ticker: dict) -> Optional[dict]:
    """24h暴涨后从高点回落 → 利好兑现即抛，熊市反弹结束"""
    cfg = get_strategy_config("pump_short")
    change_pct = float(ticker.get("priceChangePercent", 0))

    if change_pct <= cfg["min_pump"]:
        return None

    klines = Market.klines(ticker["symbol"], "1h", 6)
    if not klines or len(klines) < 3:
        return None

    highs = [float(k[2]) for k in klines]
    closes = [float(k[4]) for k in klines]
    current = closes[-1]
    peak = max(highs)

    pullback = (peak - current) / peak * 100
    if pullback < cfg["min_pullback"]:
        return None

    if change_pct >= 80 and pullback >= 15:
        strength = "S"
    elif change_pct >= 40 and pullback >= 10:
        strength = "A"
    else:
        strength = "B"

    return {
        "type": "pump_short",
        "direction": "short",
        "strength": "B",
        "reason": f"24h暴涨{change_pct:.1f}%后回落{pullback:.1f}% 历史回调概率>85%",
        "sl_pct": cfg["sl_pct"],
        "tp_pct": cfg["tp_pct"],
        "change_24h": change_pct,
    }


def detect_all(symbol: str, ticker: dict, funding_rates: dict) -> list:
    """运行所有策略检测"""
    rate = funding_rates.get(symbol, 0)
    signals = []

    for detector in [
        detect_extreme_negative_funding,
        detect_extreme_positive_funding,
        detect_crash_bounce,
        detect_pump_short,
    ]:
        # 不同detector签名不同，分开处理
        if detector in (detect_extreme_negative_funding,
                        detect_extreme_positive_funding):
            sig = detector(symbol, rate, funding_rates)
        else:
            sig = detector(ticker)
        if sig:
            sig["symbol"] = symbol
            sig["price"] = float(ticker["lastPrice"])
            sig["volume_m"] = float(ticker.get("quoteVolume", 0)) / 1e6
            signals.append(sig)

    # Sort by strength
    order = {"S": 0, "A": 1, "B": 2}
    signals.sort(key=lambda x: order.get(x["strength"], 3))
    return signals
