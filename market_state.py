"""
Market State Classifier.

Classifies the current market state into categories:
- "trending" (ADX > 25, directional)
- "ranging" (ADX < 20, oscillation)
- "volatile" (ATR% high)
This allows experience retrieval to match context (e.g., don't apply ranging lessons to a trend).
"""
import math

try:
    from .market import Market
    from .config import ATR_LOOKBACK
except ImportError:
    from market import Market
    from config import ATR_LOOKBACK


def classify_market_state(symbol: str) -> dict:
    """
    Returns a classification of the market state for the given symbol.
    {
        "state": "trending" | "ranging" | "volatile",
        "adx": float,
        "atr_pct": float,
        "trend_direction": "up" | "down" | "neutral"
    }
    """
    klines = Market.klines(symbol, "4h", 50)
    if not klines or len(klines) < 30:
        return {"state": "unknown", "adx": 0, "atr_pct": 0, "trend_direction": "neutral"}

    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    closes = [float(k[4]) for k in klines]

    # Calculate ATR%
    atr_values = []
    for i in range(1, len(klines)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        atr_values.append(tr)
    atr = sum(atr_values) / len(atr_values)
    atr_pct = (atr / closes[-1]) * 100

    # Calculate ADX (Simplified Wilder's smoothing)
    adx = _calculate_adx(highs, lows, closes)

    # Trend direction (Simple Moving Average Crossover)
    sma_10 = sum(closes[-10:]) / 10
    sma_30 = sum(closes[-30:]) / 30
    if sma_10 > sma_30 * 1.01:
        trend_dir = "up"
    elif sma_10 < sma_30 * 0.99:
        trend_dir = "down"
    else:
        trend_dir = "neutral"

    if atr_pct > 3.0:
        state = "volatile"
    elif adx > 25:
        state = "trending"
    else:
        state = "ranging"

    return {
        "state": state,
        "adx": round(adx, 2),
        "atr_pct": round(atr_pct, 2),
        "trend_direction": trend_dir
    }


def _calculate_adx(highs: list, lows: list, closes: list, period: int = 14) -> float:
    """Simplified ADX calculation."""
    plus_dm = []
    minus_dm = []
    for i in range(1, len(highs)):
        high_diff = highs[i] - highs[i - 1]
        low_diff = lows[i - 1] - lows[i]
        if high_diff > low_diff and high_diff > 0:
            plus_dm.append(high_diff)
        else:
            plus_dm.append(0)
        if low_diff > high_diff and low_diff > 0:
            minus_dm.append(low_diff)
        else:
            minus_dm.append(0)

    atr = []
    for i in range(1, len(highs)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        atr.append(tr)

    if len(atr) < period:
        return 0.0

    smoothed_plus_dm = sum(plus_dm[:period])
    smoothed_minus_dm = sum(minus_dm[:period])
    smoothed_tr = sum(atr[:period])

    plus_di = (smoothed_plus_dm / smoothed_tr) * 100 if smoothed_tr > 0 else 0
    minus_di = (smoothed_minus_dm / smoothed_tr) * 100 if smoothed_tr > 0 else 0

    dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100 if (plus_di + minus_di) > 0 else 0
    
    # Simplified ADX is just DX for now, or average of DX over time
    # For a full ADX, we'd need to smooth DX over 14 periods.
    return dx
