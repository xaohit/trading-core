"""
signal_engine.py — Phase 3 顺势信号引擎

核心理念：不预测市场，而是确认趋势已形成后顺应入场。
资金费率是情绪指标，用于判断"大众错在哪里"，而非直接预测方向。

六类信号：
  trend_long      — 顺势做多：趋势已确认，回调入场
  trend_short     — 顺势做空：下跌趋势已确认，回调放空
  momentum_long   — 动量做多：快速上涨中，趋势强劲
  momentum_short  — 动量做空：快速下跌中，下跌强劲
  funding_long    — 资金费率顺势做多：大众做空被收割+趋势已成型
  funding_short   — 资金费率顺势做空：大众做多被收割+趋势已成型

没有逆势抄底，没有"跌多了该涨"。
"""

from typing import Optional
import sys, os
try:
    from market import Market
except ImportError:
    from .market import Market

# ── MA 周期配置 ──────────────────────────────────────────────────────────
_MA_FAST   = 10    # 快速均线
_MA_SLOW   = 30    # 慢速均线

# ── 趋势确认阈值 ─────────────────────────────────────────────────────────
_ADX_TRENDING = 20       # ADX > 20 确认趋势存在
_MA_BULL_THRESHOLD = 0.0 # ma_fast > ma_slow → 多头排列
_MA_BEAR_THRESHOLD = 0.0 # ma_fast < ma_slow → 空头排列

# ── 动量阈值 ─────────────────────────────────────────────────────────────
_MOMENTUM_STRONG  = 5.0   # % | 强势动量（1h）
_MOMENTUM_MODERATE = 2.0  # % | 中等动量（1h）

# ── RSI ─────────────────────────────────────────────────────────────────
_RSI_NEUTRAL_MIN = 40
_RSI_NEUTRAL_MAX = 60

# ── 资金费率阈值 ─────────────────────────────────────────────────────────
_NEG_FUNDING = -0.03  # % 散户做空被收割
_POS_FUNDING = 0.03  # % 散户做多被收割

# ── 成交量 ────────────────────────────────────────────────────────────────
_VOLUME_SPIKE = 1.5   # 相比24h均值放大倍数


def _ma(values: list, period: int) -> Optional[float]:
    """简单移动平均"""
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _compute_rsi(closes: list, period: int = 14) -> Optional[float]:
    """RSI"""
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    return 100 - (100 / (1 + avg_gain / avg_loss))


def _compute_adx(highs: list, lows: list, closes: list, period: int = 14) -> Optional[float]:
    """简化 ADX：方向强度代理"""
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
    plus_di = (sum(plus_dm[-period:]) / atr / period) * 100
    minus_di = (sum(minus_dm[-period:]) / atr / period) * 100
    if plus_di + minus_di == 0:
        return 0
    return abs(plus_di - minus_di) / (plus_di + minus_di) * 100


def _momentum(klines: list, lookback: int = 4) -> Optional[float]:
    """价格变化%（最近 lookback 根 vs 前 lookback 根）"""
    if not klines or len(klines) < lookback * 2 + 1:
        return None
    try:
        recent = float(klines[-1][4])
        past   = float(klines[-(lookback + 1)][4])
        return ((recent - past) / past * 100) if past else None
    except (IndexError, TypeError, ValueError):
        return None


def _volume_ratio(klines: list) -> Optional[float]:
    """成交量比：最近6根 / 24根均值"""
    if not klines or len(klines) < 24:
        return None
    try:
        recent = sum(float(klines[i][5]) for i in range(-6, 0)) / 6
        avg    = sum(float(klines[i][5]) for i in range(-24, 0)) / 24
        return recent / avg if avg > 0 else None
    except (IndexError, TypeError, ValueError):
        return None


def _trend_direction(klines: list) -> dict:
    """
    返回趋势方向摘要
    包含: ma_fast, ma_slow, ma_bullish, adx, adx_trending, trend
    """
    if not klines or len(klines) < 35:
        return {}
    try:
        closes = [float(k[4]) for k in klines]
        highs  = [float(k[2]) for k in klines]
        lows   = [float(k[3]) for k in klines]

        ma_f = _ma(closes, _MA_FAST)
        ma_s = _ma(closes, _MA_SLOW)
        adx  = _compute_adx(highs, lows, closes)
        mom  = _momentum(klines, lookback=4)
        rsi  = _compute_rsi(closes)

        bull = (ma_f is not None and ma_s is not None and ma_f > ma_s * (1 + _MA_BULL_THRESHOLD / 100))
        bear = (ma_f is not None and ma_s is not None and ma_f < ma_s * (1 - _MA_BULL_THRESHOLD / 100))
        trending = adx is not None and adx > _ADX_TRENDING

        if bull and trending:
            trend = "bull"
        elif bear and trending:
            trend = "bear"
        elif bull:
            trend = "weak_bull"
        elif bear:
            trend = "weak_bear"
        else:
            trend = "neutral"

        return {
            "ma_fast": round(ma_f, 8) if ma_f else None,
            "ma_slow": round(ma_s, 8) if ma_s else None,
            "ma_bullish": bull,
            "ma_bearish": bear,
            "adx": round(adx, 1) if adx else None,
            "adx_trending": trending,
            "trend": trend,
            "momentum": round(mom, 2) if mom else None,
            "rsi": round(rsi, 1) if rsi else None,
        }
    except Exception:
        return {}


def find_signals(tickers: list, funding_rates: dict,
                 fear_greed: Optional[int] = None,
                 preloaded_klines: dict = None) -> list:
    """
    Phase 3 顺势信号引擎

    原则：
    - 不预测顶底，顺势而为
    - 资金费率是情绪背景，不直接决定方向
    - 必须先有趋势，再找入场点

    返回信号列表，每项:
      {symbol, type, direction, reason, score, strength,
       trend, momentum, adx, rsi, volume_ratio, funding_rate}
    """
    signals = []

    for ticker in tickers:
        symbol = ticker.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue

        rate = funding_rates.get(symbol, 0)
        change_24h = float(ticker.get("priceChangePercent", 0))

        # ── 拉取 K 线（预加载或按需） ─────────────────────────────────
        if preloaded_klines and symbol in preloaded_klines:
            k24 = preloaded_klines[symbol].get("1h_24")
            k6  = preloaded_klines[symbol].get("1h_6")
        else:
            k24 = Market.klines(symbol, "1h", 24)
            k6  = Market.klines(symbol, "1h", 6)

        # ── 趋势分析 ────────────────────────────────────────────────
        trend_data = _trend_direction(k24)
        mom_1h     = _momentum(k6, lookback=1) if k6 else None
        vol_ratio  = _volume_ratio(k24) if k24 else None
        rsi_val    = trend_data.get("rsi")
        adx_val    = trend_data.get("adx")
        trend      = trend_data.get("trend", "neutral")
        ma_bull    = trend_data.get("ma_bullish", False)
        ma_bear    = trend_data.get("ma_bearish", False)

        # ────────────────────────────────────────────────────────────
        # 信号1: trend_long — 均线多头排列 + ADX 确认趋势 + RSI 回调
        #   "等价格回调到均线支撑再入场" = 顺应已形成的上涨趋势
        # ────────────────────────────────────────────────────────────
        if ma_bull and trend in ("bull", "weak_bull") and adx_val and adx_val > _ADX_TRENDING:
            score = 0.5
            strength = "A"

            # 回调入场：RSI 回调到 40-50 区间（健康调整）= 最佳买点
            if rsi_val is not None and _RSI_NEUTRAL_MIN <= rsi_val <= _RSI_NEUTRAL_MAX:
                score += 0.25
            elif rsi_val is not None and rsi_val < _RSI_NEUTRAL_MIN:
                score += 0.15  # 弱势回调

            # 成交量确认
            if vol_ratio and vol_ratio >= _VOLUME_SPIKE:
                score += 0.15
                strength = "S"

            # 1h 动量健康向上
            if mom_1h and 0 < mom_1h < _MOMENTUM_STRONG:
                score += 0.1

            if score >= 0.7:
                strength = "S"

            signals.append({
                "symbol": symbol,
                "type": "trend_long",
                "direction": "long",
                "reason": f"MA多头排列+ADX确认上涨趋势({adx_val})",
                "score": round(min(score, 1.0), 2),
                "strength": strength,
                "trend": trend,
                "momentum_1h": round(mom_1h, 2) if mom_1h else None,
                "adx": adx_val,
                "rsi": rsi_val,
                "volume_ratio": round(vol_ratio, 2) if vol_ratio else None,
                "funding_rate": rate,
                "change_24h": change_24h,
                "ma_fast": trend_data.get("ma_fast"),
                "ma_slow": trend_data.get("ma_slow"),
            })

        # ────────────────────────────────────────────────────────────
        # 信号2: trend_short — 均线空头排列 + ADX 确认下跌 + RSI 反弹
        #   "等反弹到均线压力再入场做空" = 顺应已形成的下跌趋势
        # ────────────────────────────────────────────────────────────
        if ma_bear and trend in ("bear", "weak_bear") and adx_val and adx_val > _ADX_TRENDING:
            score = 0.5
            strength = "A"

            # 反弹入场：RSI 反弹到 50-60 区间（弱势反弹）= 最佳做空点
            if rsi_val is not None and _RSI_NEUTRAL_MAX >= rsi_val >= _RSI_NEUTRAL_MIN:
                score += 0.25
            elif rsi_val is not None and rsi_val > _RSI_NEUTRAL_MAX:
                score += 0.15  # 强势反弹，可能还有空间

            # 成交量确认
            if vol_ratio and vol_ratio >= _VOLUME_SPIKE:
                score += 0.15
                strength = "S"

            # 1h 动量健康向下
            if mom_1h and -_MOMENTUM_STRONG < mom_1h < 0:
                score += 0.1

            if score >= 0.7:
                strength = "S"

            signals.append({
                "symbol": symbol,
                "type": "trend_short",
                "direction": "short",
                "reason": f"MA空头排列+ADX确认下跌趋势({adx_val})",
                "score": round(min(score, 1.0), 2),
                "strength": strength,
                "trend": trend,
                "momentum_1h": round(mom_1h, 2) if mom_1h else None,
                "adx": adx_val,
                "rsi": rsi_val,
                "volume_ratio": round(vol_ratio, 2) if vol_ratio else None,
                "funding_rate": rate,
                "change_24h": change_24h,
                "ma_fast": trend_data.get("ma_fast"),
                "ma_slow": trend_data.get("ma_slow"),
            })

        # ────────────────────────────────────────────────────────────
        # 信号3: momentum_long — 强势动量 + RSI 不过热 + 趋势已成型
        #   快速拉升中，不追涨，只做回调
        # ────────────────────────────────────────────────────────────
        if mom_1h and mom_1h >= _MOMENTUM_STRONG and trend not in ("bear", "weak_bear"):
            score = 0.4
            strength = "A"

            # RSI 不过热（不超过70）
            if rsi_val is not None and rsi_val < _RSI_NEUTRAL_MAX:
                score += 0.2
                if rsi_val < 55:
                    score += 0.1  # 健康上涨

            # 成交量放大
            if vol_ratio and vol_ratio >= _VOLUME_SPIKE:
                score += 0.2
                strength = "S"

            # ADX 趋势确认加分
            if adx_val and adx_val > _ADX_TRENDING:
                score += 0.1

            if score >= 0.8:
                strength = "S"

            signals.append({
                "symbol": symbol,
                "type": "momentum_long",
                "direction": "long",
                "reason": f"强势动量+{mom_1h:.1f}%，RSI={rsi_val}",
                "score": round(min(score, 1.0), 2),
                "strength": strength,
                "trend": trend,
                "momentum_1h": round(mom_1h, 2),
                "adx": adx_val,
                "rsi": rsi_val,
                "volume_ratio": round(vol_ratio, 2) if vol_ratio else None,
                "funding_rate": rate,
                "change_24h": change_24h,
            })

        # ────────────────────────────────────────────────────────────
        # 信号4: momentum_short — 强势下跌动量 + RSI 不过冷 + 趋势已成型
        #   快速下跌中，不抄底，只做反弹衰竭后的顺势空
        # ────────────────────────────────────────────────────────────
        if mom_1h and mom_1h <= -_MOMENTUM_STRONG and trend not in ("bull", "weak_bull"):
            score = 0.4
            strength = "A"

            # RSI 不过冷（不低于30）
            if rsi_val is not None and rsi_val > _RSI_NEUTRAL_MIN:
                score += 0.2
                if rsi_val > 45:
                    score += 0.1  # 弱势反弹，可能继续跌

            # 成交量放大
            if vol_ratio and vol_ratio >= _VOLUME_SPIKE:
                score += 0.2
                strength = "S"

            # ADX 趋势确认加分
            if adx_val and adx_val > _ADX_TRENDING:
                score += 0.1

            if score >= 0.8:
                strength = "S"

            signals.append({
                "symbol": symbol,
                "type": "momentum_short",
                "direction": "short",
                "reason": f"强势下跌动量{mom_1h:.1f}%，RSI={rsi_val}",
                "score": round(min(score, 1.0), 2),
                "strength": strength,
                "trend": trend,
                "momentum_1h": round(mom_1h, 2),
                "adx": adx_val,
                "rsi": rsi_val,
                "volume_ratio": round(vol_ratio, 2) if vol_ratio else None,
                "funding_rate": rate,
                "change_24h": change_24h,
            })

        # ────────────────────────────────────────────────────────────
        # 信号5: funding_long — 资金费率极端 + 趋势已成型（不做逆势）
        #   "大众在做空被收割" + "趋势已在上涨" → 顺势做多
        #   绝对不：看到负费率就盲目做多
        # ────────────────────────────────────────────────────────────
        if rate <= _NEG_FUNDING and trend in ("bull", "weak_bull") and ma_bull:
            score = 0.5
            strength = "A"

            # 资金费率极端程度
            score += min(abs(rate) / 0.10, 0.2)

            # ADX 趋势确认
            if adx_val and adx_val > _ADX_TRENDING:
                score += 0.15

            # RSI 回调位（健康调整，不是转势）
            if rsi_val and _RSI_NEUTRAL_MIN <= rsi_val <= _RSI_NEUTRAL_MAX:
                score += 0.15

            if vol_ratio and vol_ratio >= _VOLUME_SPIKE:
                score += 0.1

            if score >= 0.8:
                strength = "S"

            signals.append({
                "symbol": symbol,
                "type": "funding_long",
                "direction": "long",
                "reason": f"资金费率负({rate:.3f}%)+趋势已成型+MA多头",
                "score": round(min(score, 1.0), 2),
                "strength": strength,
                "trend": trend,
                "momentum_1h": round(mom_1h, 2) if mom_1h else None,
                "adx": adx_val,
                "rsi": rsi_val,
                "volume_ratio": round(vol_ratio, 2) if vol_ratio else None,
                "funding_rate": rate,
                "change_24h": change_24h,
            })

        # ────────────────────────────────────────────────────────────
        # 信号6: funding_short — 资金费率极端 + 趋势已成型（顺势）
        #   "大众在做多被收割" + "趋势已在下跌" → 顺势做空
        # ────────────────────────────────────────────────────────────
        if rate >= _POS_FUNDING and trend in ("bear", "weak_bear") and ma_bear:
            score = 0.5
            strength = "A"

            # 资金费率极端程度
            score += min(rate / 0.10, 0.2)

            # ADX 趋势确认
            if adx_val and adx_val > _ADX_TRENDING:
                score += 0.15

            # RSI 反弹位
            if rsi_val and _RSI_NEUTRAL_MAX >= rsi_val >= _RSI_NEUTRAL_MIN:
                score += 0.15

            if vol_ratio and vol_ratio >= _VOLUME_SPIKE:
                score += 0.1

            if score >= 0.8:
                strength = "S"

            signals.append({
                "symbol": symbol,
                "type": "funding_short",
                "direction": "short",
                "reason": f"资金费率正({rate:.3f}%)+趋势已成型+MA空头",
                "score": round(min(score, 1.0), 2),
                "strength": strength,
                "trend": trend,
                "momentum_1h": round(mom_1h, 2) if mom_1h else None,
                "adx": adx_val,
                "rsi": rsi_val,
                "volume_ratio": round(vol_ratio, 2) if vol_ratio else None,
                "funding_rate": rate,
                "change_24h": change_24h,
            })

    # ── 去重 + 排序 ─────────────────────────────────────────────────────
    seen = {}
    for sig in signals:
        key = (sig["symbol"], sig["direction"])
        if key not in seen or sig["score"] > seen[key]["score"]:
            seen[key] = sig

    deduped = list(seen.values())
    deduped.sort(key=lambda x: x["score"], reverse=True)
    return deduped


# ── Phase 4 占位符：LLM 仲裁 ─────────────────────────────────────────────
def llm_arbitrate(signals: list, symbol: str) -> dict:
    """
    多信号冲突时调用 LLM 做最终仲裁（Phase 4）
    目前返回最高分信号
    """
    if not signals:
        return {}
    return max(signals, key=lambda x: x["score"])
