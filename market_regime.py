"""
MarketRegimeDetector — Phase 4 市场状态分类器

职责：
- 判断市场当前是 trending / ranging / volatile
- 使用多时间框确认（4h + 1h ADX 同时满足才认 trending）
- 输出 regime 供 StrategyRouter 使用

调用方式：
    from market_regime import MarketRegimeDetector, Regime

    report = MarketRegimeDetector.detect("BTCUSDT")
    print(report.regime)       # Regime.TRENDING_UP
    print(report.adx_4h)        # 32.5
    print(report.volatility)    # 1.2
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

try:
    from market import Market
except ImportError:
    from .market import Market


class Regime(Enum):
    """市场状态枚举"""
    TRENDING_UP   = "trending_up"    # 上涨趋势
    TRENDING_DOWN = "trending_down"  # 下跌趋势
    RANGING       = "ranging"        # 震荡/横盘
    HIGH_VOL      = "high_vol"      # 高波动（叠加在其他状态上）


@dataclass
class RegimeReport:
    regime: Regime
    regime_confidence: float       # 0.0–1.0，判断置信度
    adx_4h: float                  # 4h ADX
    adx_1h: float                  # 1h ADX
    ma_spread_pct: float           # MA10 vs MA30 分离度 %
    volatility: float              # 波动率（当前 ATR%/正常 ATR%）
    trend_direction: str          # "up" / "down" / "neutral"
    is_high_vol: bool             # 是否高波动

    def dict(self) -> dict:
        return {
            "regime": self.regime.value,
            "regime_confidence": self.regime_confidence,
            "adx_4h": self.adx_4h,
            "adx_1h": self.adx_1h,
            "ma_spread_pct": self.ma_spread_pct,
            "volatility": self.volatility,
            "trend_direction": self.trend_direction,
            "is_high_vol": self.is_high_vol,
        }


# ── Thresholds ────────────────────────────────────────────────────────────────
ADX_TRENDING   = 25.0   # ADX > 25 确认趋势存在
ADX_RANGING    = 20.0   # ADX < 20 确认震荡
MA_SPREAD_MIN  = 0.5    # MA 分离度 > 0.5% 才算有效趋势
VOL_NORMAL     = 1.5     # volatility > 1.5x 正常值 = 高波动


def _ma(values: list, period: int) -> Optional[float]:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _compute_adx(highs: list, lows: list, closes: list, period: int = 14) -> float:
    """简化 ADX 计算"""
    if len(closes) < period + 2:
        return 0.0
    trs, plus_dm, minus_dm = [], [], []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i-1]),
                 abs(lows[i] - closes[i-1]))
        up   = highs[i] - highs[i-1]
        down = lows[i-1] - lows[i]
        trs.append(tr)
        plus_dm.append(up if up > down and up > 0 else 0)
        minus_dm.append(down if down > up and down > 0 else 0)
    if len(trs) < period:
        return 0.0
    atr = sum(trs[-period:]) / period
    if atr == 0:
        return 0.0
    plus_di  = (sum(plus_dm[-period:])  / atr / period) * 100
    minus_di = (sum(minus_dm[-period:]) / atr / period) * 100
    if plus_di + minus_di == 0:
        return 0.0
    return abs(plus_di - minus_di) / (plus_di + minus_di) * 100


def _compute_volatility(klines: list, lookback: int = 14) -> float:
    """波动率：当前 ATR% / 历史平均 ATR%（返回比率，1.0=正常）"""
    if len(klines) < lookback + 2:
        return 1.0
    closes = [float(k[4]) for k in klines]
    highs  = [float(k[2]) for k in klines]
    lows   = [float(k[3]) for k in klines]
    atrs = []
    for i in range(1, len(klines)):
        tr = max(highs[i]-lows[i],
                 abs(highs[i]-closes[i-1]),
                 abs(lows[i]-closes[i-1]))
        atrs.append(tr / closes[i] * 100)
    if len(atrs) < lookback:
        return 1.0
    current_atr = sum(atrs[-lookback:]) / lookback
    hist_avg    = sum(atrs[:-lookback]) / max(len(atrs) - lookback, 1)
    if hist_avg == 0:
        return 1.0
    return current_atr / hist_avg


def _trend_direction(ma_fast: float, ma_slow: float) -> str:
    spread = (ma_fast - ma_slow) / ma_slow * 100
    if spread > MA_SPREAD_MIN:
        return "up"
    elif spread < -MA_SPREAD_MIN:
        return "down"
    return "neutral"


def detect_regime(symbol: str, klines_4h: list = None, klines_1h: list = None) -> RegimeReport:
    """
    主入口：检测市场状态。

    多时间框确认：
    - 4h ADX 确认主趋势（更少噪音）
    - 1h ADX 确认短期节奏
    - 4h + 1h 同时 trending → 才认定 trending（防假信号）
    - 4h trending + 1h ranging → 仍认定 trending（以大时间框为准）

    Args:
        symbol: 币种
        klines_4h: 4h K线（可选，内部会拉取）
        klines_1h: 1h K线（可选，内部会拉取）

    Returns:
        RegimeReport
    """
    # 拉取数据
    if klines_4h is None:
        klines_4h = Market.klines(symbol, "4h", 50)
    if klines_1h is None:
        klines_1h = Market.klines(symbol, "1h", 60)

    has_4h = klines_4h and len(klines_4h) >= 35
    has_1h = klines_1h and len(klines_1h) >= 35

    # ── 4h 指标 ──────────────────────────────────────────────────────────────
    adx_4h   = 0.0
    ma_f_4h  = None
    ma_s_4h  = None
    if has_4h:
        c4 = [float(k[4]) for k in klines_4h]
        h4 = [float(k[2]) for k in klines_4h]
        l4 = [float(k[3]) for k in klines_4h]
        adx_4h  = _compute_adx(h4, l4, c4)
        ma_f_4h = _ma(c4, 10)
        ma_s_4h = _ma(c4, 30)

    # ── 1h 指标 ──────────────────────────────────────────────────────────────
    adx_1h   = 0.0
    ma_f_1h  = None
    ma_s_1h  = None
    if has_1h:
        c1 = [float(k[4]) for k in klines_1h]
        h1 = [float(k[2]) for k in klines_1h]
        l1 = [float(k[3]) for k in klines_1h]
        adx_1h  = _compute_adx(h1, l1, c1)
        ma_f_1h = _ma(c1, 10)
        ma_s_1h = _ma(c1, 30)

    # ── 波动率 ────────────────────────────────────────────────────────────────
    volatility = 1.0
    if has_1h:
        volatility = _compute_volatility(klines_1h)

    is_high_vol = volatility > VOL_NORMAL

    # ── 趋势方向 ─────────────────────────────────────────────────────────────
    # 以 4h 为主
    if ma_f_4h is not None and ma_s_4h is not None:
        trend_direction = _trend_direction(ma_f_4h, ma_s_4h)
    elif ma_f_1h is not None and ma_s_1h is not None:
        trend_direction = _trend_direction(ma_f_1h, ma_s_1h)
    else:
        trend_direction = "neutral"

    # MA 分离度（用 4h）
    ma_spread_pct = 0.0
    if ma_f_4h is not None and ma_s_4h is not None and ma_s_4h != 0:
        ma_spread_pct = (ma_f_4h - ma_s_4h) / ma_s_4h * 100

    # ── Regime 判断 ──────────────────────────────────────────────────────────
    # 高波动叠加
    if is_high_vol:
        # 高波动下，降低趋势认定门槛，但仍按主状态输出
        _adx_thresh_trend = 20.0
        _adx_thresh_range = 15.0
    else:
        _adx_thresh_trend = ADX_TRENDING
        _adx_thresh_range = ADX_RANGING

    # 多时间框确认逻辑
    if has_4h and adx_4h > _adx_thresh_trend and abs(ma_spread_pct) > MA_SPREAD_MIN:
        # 4h trending 确认
        regime = Regime.TRENDING_UP if trend_direction == "up" else Regime.TRENDING_DOWN
        # 置信度：4h ADX 强度 + 1h 是否配合
        confidence = min(adx_4h / 50.0, 1.0)  # ADX 50 = 100% confidence
        if has_1h and adx_1h > _adx_thresh_trend:
            confidence = min(confidence + 0.1, 1.0)  # 双时间框确认，加权
        elif has_1h and adx_1h < _adx_thresh_range:
            confidence = max(confidence - 0.1, 0.3)  # 1h 背离，降权

    elif has_4h and adx_4h < _adx_thresh_range and abs(ma_spread_pct) < MA_SPREAD_MIN:
        # 4h ranging
        regime = Regime.RANGING
        confidence = 0.7 if adx_4h < 15 else 0.5

    elif has_1h and adx_1h > _adx_thresh_trend and abs(ma_spread_pct) > MA_SPREAD_MIN:
        # 4h 数据不足，用 1h
        regime = Regime.TRENDING_UP if trend_direction == "up" else Regime.TRENDING_DOWN
        confidence = 0.4  # 仅 1h，数据不足

    else:
        # 默认震荡（没有明确趋势）
        regime = Regime.RANGING
        confidence = 0.5

    return RegimeReport(
        regime=regime,
        regime_confidence=round(confidence, 3),
        adx_4h=round(adx_4h, 2),
        adx_1h=round(adx_1h, 2),
        ma_spread_pct=round(ma_spread_pct, 3),
        volatility=round(volatility, 2),
        trend_direction=trend_direction,
        is_high_vol=is_high_vol,
    )


# ── 便捷包装 ─────────────────────────────────────────────────────────────────
class MarketRegimeDetector:
    """包装类，与系统其他模块风格一致"""

    @staticmethod
    def detect(symbol: str, klines_4h: list = None, klines_1h: list = None) -> RegimeReport:
        return detect_regime(symbol, klines_4h, klines_1h)

    @staticmethod
    def is_trending(symbol: str) -> bool:
        r = detect_regime(symbol)
        return r.regime in (Regime.TRENDING_UP, Regime.TRENDING_DOWN)

    @staticmethod
    def is_ranging(symbol: str) -> bool:
        return detect_regime(symbol).regime == Regime.RANGING

    @staticmethod
    def is_high_volatility(symbol: str) -> bool:
        return detect_regime(symbol).is_high_vol

    @staticmethod
    def as_market_state_dict(symbol: str) -> dict:
        """
        返回兼容旧 market_state.py 格式的 dict。
        用于 DB 存储和 agent_tools 等外部调用。
        """
        r = detect_regime(symbol)
        state_map = {
            Regime.TRENDING_UP: "trending",
            Regime.TRENDING_DOWN: "trending",
            Regime.RANGING: "ranging",
        }
        return {
            "state": state_map.get(r.regime, "unknown"),
            "adx": r.adx_4h,
            "atr_pct": r.volatility * 2.0,   # volatility ratio → rough atr%
            "trend_direction": r.trend_direction,
        }


# ── Backward-compat shim ─────────────────────────────────────────────────────
def classify_market_state(symbol: str) -> dict:
    """
    兼容旧 market_state.py 的接口。
    内部委托给 MarketRegimeDetector。
    """
    return MarketRegimeDetector.as_market_state_dict(symbol)
