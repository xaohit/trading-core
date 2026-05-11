"""
Strategy Detectors — Phase 3 顺势检测器
每个检测器在产生信号前必须确认趋势方向，
不再做逆势抄底/摸顶。
"""
from typing import Optional

try:
    from ..config import get_strategy_config, STRENGTH_S, STRENGTH_A
    from ..market import Market
except ImportError:
    from config import get_strategy_config, STRENGTH_S, STRENGTH_A
    from market import Market


# ── 顺势Trend Helper ────────────────────────────────────────────────────
_MA_FAST_PERIOD  = 10
_MA_SLOW_PERIOD  = 30
_ADX_PERIOD      = 14
_ADX_TRENDING    = 20.0   # ADX > 20 确认趋势存在


def _ma(values: list, period: int) -> Optional[float]:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _compute_adx(highs: list, lows: list, closes: list, period: int = 14) -> Optional[float]:
    """简化 ADX：方向强度"""
    if len(closes) < period + 2:
        return None
    trs, plus_dm, minus_dm = [], [], []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i-1]),
                 abs(lows[i] - closes[i-1]))
        up = highs[i] - highs[i-1]
        down = lows[i-1] - lows[i]
        trs.append(tr)
        plus_dm.append(up if up > down and up > 0 else 0)
        minus_dm.append(down if down > up and down > 0 else 0)
    if len(trs) < period:
        return None
    atr = sum(trs[-period:]) / period
    if atr == 0:
        return 0
    plus_di  = (sum(plus_dm[-period:])  / atr / period) * 100
    minus_di = (sum(minus_dm[-period:]) / atr / period) * 100
    if plus_di + minus_di == 0:
        return 0
    return abs(plus_di - minus_di) / (plus_di + minus_di) * 100


def _trend_ok_for_long(klines_24: list) -> tuple[bool, Optional[float]]:
    """
    检查是否适合做多（顺势）。
    Returns (trend_ok, adx_value)
    trend_ok=True: ma_fast > ma_slow AND ADX > threshold
    """
    if not klines_24 or len(klines_24) < _MA_SLOW_PERIOD + 2:
        return False, None
    try:
        closes = [float(k[4]) for k in klines_24]
        highs  = [float(k[2]) for k in klines_24]
        lows   = [float(k[3]) for k in klines_24]
        ma_f   = _ma(closes, _MA_FAST_PERIOD)
        ma_s   = _ma(closes, _MA_SLOW_PERIOD)
        adx    = _compute_adx(highs, lows, closes, _ADX_PERIOD)
        if ma_f is None or ma_s is None:
            return False, adx
        # 多头排列：快速均线在慢速均线上方，且有明显分离（>0.5%）
        bull_separation = (ma_f - ma_s) / ma_s * 100
        bull = bull_separation > 0.3 and adx is not None and adx > _ADX_TRENDING
        return bull, adx
    except Exception:
        return False, None


def _trend_ok_for_short(klines_24: list) -> tuple[bool, Optional[float]]:
    """
    检查是否适合做空（顺势）。
    Returns (trend_ok, adx_value)
    """
    if not klines_24 or len(klines_24) < _MA_SLOW_PERIOD + 2:
        return False, None
    try:
        closes = [float(k[4]) for k in klines_24]
        highs  = [float(k[2]) for k in klines_24]
        lows   = [float(k[3]) for k in klines_24]
        ma_f   = _ma(closes, _MA_FAST_PERIOD)
        ma_s   = _ma(closes, _MA_SLOW_PERIOD)
        adx    = _compute_adx(highs, lows, closes, _ADX_PERIOD)
        if ma_f is None or ma_s is None:
            return False, adx
        # 空头排列：快速均线在慢速均线下方，且有明显分离
        bear_separation = (ma_s - ma_f) / ma_s * 100
        bear = bear_separation > 0.3 and adx is not None and adx > _ADX_TRENDING
        return bear, adx
    except Exception:
        return False, None


# ── Detector 1: neg_funding_long ────────────────────────────────────────
def detect_extreme_negative_funding(symbol: str, rate: float,
                                     funding_rates: dict,
                                     klines: list = None) -> Optional[dict]:
    """
    资金费率极度为负 + 趋势已成型（顺势）→ 做多
    不再盲目"跌多了做多"，必须先确认趋势向上。
    """
    cfg = get_strategy_config("neg_funding_long")
    if rate >= cfg["min_rate"]:
        return None

    if klines is None:
        klines = Market.klines(symbol, "1h", 32)
    if not klines or len(klines) < 35:
        return None

    # ── 趋势确认（必须） ─────────────────────────────────────────────
    trend_ok, adx = _trend_ok_for_long(klines)
    if not trend_ok:
        return None   # 趋势未成型，拒绝逆势入场

    closes = [float(k[4]) for k in klines]
    change_pct = (closes[-1] - closes[0]) / closes[0] * 100

    # 允许在上涨途中出现负费率（回调），但不接受下跌中的负费率
    if change_pct <= -5:
        return None  # 还在下跌，不做逆势

    if rate <= STRENGTH_S["neg_funding"] and adx and adx > _ADX_TRENDING + 5:
        strength = "S"
    elif rate <= STRENGTH_A["neg_funding"] and trend_ok:
        strength = "A"
    else:
        strength = "B"

    return {
        "type": "neg_funding_long",
        "direction": "long",
        "strength": strength,
        "reason": f"费率{rate:.3f}%+顺势MA多头+ADX确认({adx:.0f})",
        "sl_pct": cfg["sl_pct"],
        "tp_pct": cfg["tp_pct"],
        "funding_rate": rate,
        "change_24h": change_pct,
        "adx": round(adx, 1) if adx else None,
    }


# ── Detector 2: pos_funding_short ───────────────────────────────────────
def detect_extreme_positive_funding(symbol: str, rate: float,
                                     funding_rates: dict,
                                     klines: list = None) -> Optional[dict]:
    """
    资金费率极度为正 + 趋势已成型（顺势）→ 做空
    不再盲目"涨多了做空"，必须先确认趋势向下。
    """
    cfg = get_strategy_config("pos_funding_short")
    if rate <= cfg["min_rate"]:
        return None

    if klines is None:
        klines = Market.klines(symbol, "1h", 32)
    if not klines or len(klines) < 35:
        return None

    # ── 趋势确认（必须） ─────────────────────────────────────────────
    trend_ok, adx = _trend_ok_for_short(klines)
    if not trend_ok:
        return None   # 趋势未成型，拒绝逆势入场

    closes = [float(k[4]) for k in klines]
    change_pct = (closes[-1] - closes[0]) / closes[0] * 100

    # 允许在下跌途中出现正费率（反弹），但不接受上涨中的正费率
    if change_pct >= 5:
        return None  # 还在上涨，不做逆势

    if rate >= STRENGTH_S["pos_funding"] and adx and adx > _ADX_TRENDING + 5:
        strength = "S"
    elif rate >= STRENGTH_A["pos_funding"] and trend_ok:
        strength = "A"
    else:
        strength = "B"

    return {
        "type": "pos_funding_short",
        "direction": "short",
        "strength": strength,
        "reason": f"费率{rate:.3f}%+顺势MA空头+ADX确认({adx:.0f})",
        "sl_pct": cfg["sl_pct"],
        "tp_pct": cfg["tp_pct"],
        "funding_rate": rate,
        "change_24h": change_pct,
        "adx": round(adx, 1) if adx else None,
    }


# ── Detector 3: crash_bounce_long ──────────────────────────────────────
def detect_crash_bounce(ticker: dict, klines: list = None) -> Optional[dict]:
    """
    24h暴跌后反弹 → 顺势做多（不是抄底，是等反弹确认后追涨）
    """
    cfg = get_strategy_config("crash_bounce_long")
    change_pct = float(ticker.get("priceChangePercent", 0))

    if change_pct >= cfg["min_crash"]:
        return None

    if klines is None:
        klines = Market.klines(ticker["symbol"], "1h", 32)
    if not klines or len(klines) < 35:
        return None

    # ── 趋势确认：价格已经在 MA 多头排列上方（反弹已确认）────────────
    trend_ok, adx = _trend_ok_for_long(klines)
    if not trend_ok:
        return None

    lows    = [float(k[3]) for k in klines]
    closes  = [float(k[4]) for k in klines]
    current = closes[-1]
    bottom  = min(lows)

    bounce = (current - bottom) / bottom * 100
    if bounce < cfg["min_bounce"]:
        return None

    if change_pct <= -20 and bounce >= 15 and adx and adx > _ADX_TRENDING + 5:
        strength = "S"
    elif change_pct <= -10 and bounce >= 8 and trend_ok:
        strength = "A"
    else:
        strength = "B"

    return {
        "type": "crash_bounce_long",
        "direction": "long",
        "strength": strength,
        "reason": f"24h暴跌{change_pct:.1f}%后反弹{bounce:.1f}%+顺势MA多头",
        "sl_pct": cfg["sl_pct"],
        "tp_pct": cfg["tp_pct"],
        "change_24h": change_pct,
        "adx": round(adx, 1) if adx else None,
    }


# ── Detector 4: pump_short ─────────────────────────────────────────────
def detect_pump_short(ticker: dict, klines: list = None) -> Optional[dict]:
    """
    24h暴涨后回落 → 顺势做空（不是摸顶，是等回落确认后追空）
    """
    cfg = get_strategy_config("pump_short")
    change_pct = float(ticker.get("priceChangePercent", 0))

    if change_pct <= cfg["min_pump"]:
        return None

    if klines is None:
        klines = Market.klines(ticker["symbol"], "1h", 32)
    if not klines or len(klines) < 35:
        return None

    # ── 趋势确认：价格已经在 MA 空头排列下方（回落已确认）────────────
    trend_ok, adx = _trend_ok_for_short(klines)
    if not trend_ok:
        return None

    highs   = [float(k[2]) for k in klines]
    closes  = [float(k[4]) for k in klines]
    current = closes[-1]
    peak    = max(highs)

    pullback = (peak - current) / peak * 100
    if pullback < cfg["min_pullback"]:
        return None

    if change_pct >= 80 and pullback >= 15 and adx and adx > _ADX_TRENDING + 5:
        strength = "S"
    elif change_pct >= 40 and pullback >= 10 and trend_ok:
        strength = "A"
    else:
        strength = "B"

    return {
        "type": "pump_short",
        "direction": "short",
        "strength": strength,
        "reason": f"24h暴涨{change_pct:.1f}%后回落{pullback:.1f}%+顺势MA空头",
        "sl_pct": cfg["sl_pct"],
        "tp_pct": cfg["tp_pct"],
        "change_24h": change_pct,
        "adx": round(adx, 1) if adx else None,
    }


# ── detect_all ─────────────────────────────────────────────────────────
def detect_all(symbol: str, ticker: dict, funding_rates: dict) -> list:
    """
    运行所有策略检测。
    klines 由 scanner 预取后通过 ticker['__klines_1h_24'] 传入。
    """
    rate = funding_rates.get(symbol, 0)
    signals = []

    klines_1h_24 = ticker.get("__klines_1h_24", [])
    klines_1h_6  = ticker.get("__klines_1h_6", [])

    for detector in [
        detect_extreme_negative_funding,
        detect_extreme_positive_funding,
        detect_crash_bounce,
        detect_pump_short,
    ]:
        if detector in (detect_extreme_negative_funding,
                        detect_extreme_positive_funding):
            sig = detector(symbol, rate, funding_rates, klines_1h_24)
        else:
            sig = detector(ticker, klines_1h_24)  # 32根 klines 用于趋势确认
        if sig:
            sig["symbol"] = symbol
            sig["price"]  = float(ticker["lastPrice"])
            sig["volume_m"] = float(ticker.get("quoteVolume", 0)) / 1e6
            signals.append(sig)

    order = {"S": 0, "A": 1, "B": 2}
    signals.sort(key=lambda x: order.get(x.get("strength"), 3))
    return signals
