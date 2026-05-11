"""
StrategyRouter — Phase 4 策略动态路由器

职责：
- 接收 detectors.py 发出的原始信号列表
- 根据当前市场状态（Regime）过滤/降权信号
- 权重从 state.json 读取（由 self_optimizer 写入），无数据时用默认表

调用方式：
    from strategy_router import StrategyRouter
    from market_regime import MarketRegimeDetector

    regime  = MarketRegimeDetector.detect("BTCUSDT")
    router  = StrategyRouter()
    filtered = router.route(signals, regime)
"""

from typing import Optional
from pathlib import Path
import json

try:
    from market_regime import Regime, RegimeReport
except ImportError:
    from .market_regime import Regime, RegimeReport

# ── 默认权重表（完全可调）─────────────────────────────────────────────────
# 0.0 = 完全抑制，1.0 = 全权重
DEFAULT_WEIGHTS: dict[Regime, dict[str, float]] = {
    Regime.TRENDING_UP: {
        "neg_funding_long":  1.0,
        "pos_funding_short": 0.0,
        "crash_bounce_long": 0.8,
        "pump_short":        0.0,
        "trend_long":        1.0,
        "trend_short":       0.0,
        "momentum_long":     1.0,
        "momentum_short":    0.0,
    },
    Regime.TRENDING_DOWN: {
        "neg_funding_long":  0.0,
        "pos_funding_short": 1.0,
        "crash_bounce_long": 0.0,
        "pump_short":        0.8,
        "trend_long":        0.0,
        "trend_short":       1.0,
        "momentum_long":     0.0,
        "momentum_short":    1.0,
    },
    Regime.RANGING: {
        "neg_funding_long":  1.0,
        "pos_funding_short": 1.0,
        "crash_bounce_long": 1.0,
        "pump_short":        1.0,
        "trend_long":        0.0,
        "trend_short":       0.0,
        "momentum_long":     0.3,
        "momentum_short":    0.3,
    },
    Regime.HIGH_VOL: {
        # 高波动叠加层：单独定义，应用时叠加到主权重上
        # 格式同主权重表，实际乘算到对应主权重
        "_is_multiplier": True,
        "neg_funding_long":  0.6,
        "pos_funding_short": 0.6,
        "crash_bounce_long": 0.6,
        "pump_short":        0.6,
        "trend_long":        0.6,
        "trend_short":       0.6,
        "momentum_long":     0.6,
        "momentum_short":    0.6,
    },
}

# 状态文件路径
STATE_PATH = Path.home() / ".hermes/trading_core/state.json"


def _load_regime_weights() -> dict[Regime, dict[str, float]]:
    """从 state.json 加载已学习的 regime 权重，无数据则返回空 dict"""
    try:
        if STATE_PATH.exists():
            data = json.loads(STATE_PATH.read_text())
            raw = data.get("regime_weights", {})
            # 反序列化：key 从字符串转回 Regime enum
            result: dict[Regime, dict[str, float]] = {}
            for k, v in raw.items():
                try:
                    regime = Regime(k)
                except ValueError:
                    continue
                result[regime] = v
            if result:
                return result
    except Exception:
        pass
    return {}


def _save_regime_weights(weights: dict[Regime, dict[str, float]]):
    """保存 regime 权重到 state.json"""
    try:
        data = {}
        if STATE_PATH.exists():
            data = json.loads(STATE_PATH.read_text())
        # 序列化：Regime enum → 字符串 key
        data["regime_weights"] = {k.value: v for k, v in weights.items()}
        STATE_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"[StrategyRouter] 警告：无法保存 regime_weights: {e}")


def _effective_weights(
    regime: Regime,
    regime_weights: dict[Regime, dict[str, float]],
) -> dict[str, float]:
    """
    获取某 regime 的有效权重。
    优先从已学习的 regime_weights 读，否则用 DEFAULT_WEIGHTS。
    """
    if regime in regime_weights:
        return regime_weights[regime]
    if regime in DEFAULT_WEIGHTS:
        return DEFAULT_WEIGHTS[regime]
    # 完全未知 regime，返回均衡权重
    return {sig: 0.5 for sig in [
        "neg_funding_long", "pos_funding_short",
        "crash_bounce_long", "pump_short",
        "trend_long", "trend_short",
        "momentum_long", "momentum_short",
    ]}


def route_signals(
    signals: list,
    regime: RegimeReport,
    regime_weights: dict[Regime, dict[str, float]] = None,
) -> list:
    """
    根据市场状态过滤 + 排序信号。

    Args:
        signals: detect_all() 返回的原始信号列表
        regime: MarketRegimeDetector.detect() 返回的报告
        regime_weights: 可选，已学习的权重 dict（默认从 state.json 加载）

    Returns:
        过滤后的信号列表，权重 = 0 的被移除
    """
    if not signals:
        return []

    if regime_weights is None:
        regime_weights = _load_regime_weights()

    weights = _effective_weights(regime.regime, regime_weights)

    # 高波动叠加乘数
    if regime.is_high_vol:
        vol_mult = DEFAULT_WEIGHTS.get(Regime.HIGH_VOL, {})
        is_mult_table = vol_mult.get("_is_multiplier", False)
    else:
        vol_mult = {}
        is_mult_table = False

    filtered = []
    for sig in signals:
        sig_type = sig.get("type", "")
        w = weights.get(sig_type, 0.0)

        # 高波动乘算
        if vol_mult and sig_type in vol_mult:
            w = w * vol_mult[sig_type]

        if w <= 0:
            continue

        sig["_router_weight"] = round(w, 3)
        sig["_regime"] = regime.regime.value
        sig["_regime_confidence"] = regime.regime_confidence
        filtered.append(sig)

    strength_order = {"S": 0, "A": 1, "B": 2}
    filtered.sort(key=lambda x: (
        strength_order.get(x.get("strength"), 3),
        -x.get("_router_weight", 0),
    ))
    return filtered


class StrategyRouter:
    """
    策略路由器。

    使用方式：
        router = StrategyRouter()
        router.set_btc_regime(regime_report)
        filtered = router.route(signals)

    权重来源（按优先级）：
        1. state.json → regime_weights（由 self_optimizer 写入）
        2. DEFAULT_WEIGHTS（硬编码默认，稳健）
    """

    def __init__(self):
        self._btc_regime: Optional[RegimeReport] = None
        self._regime_weights: dict[Regime, dict[str, float]] = _load_regime_weights()

    def set_btc_regime(self, regime: RegimeReport):
        """设置大盘 Regime（每个扫描周期刷新一次）"""
        self._btc_regime = regime

    def route(self, signals: list, regime: RegimeReport = None) -> list:
        """路由信号"""
        target = regime or self._btc_regime
        if target is None:
            return signals
        return route_signals(signals, target, self._regime_weights)

    def reload_weights(self):
        """重新从 state.json 加载权重（self_optimizer --apply 后调用）"""
        self._regime_weights = _load_regime_weights()

    @staticmethod
    def get_default_weights(regime: Regime) -> dict[str, float]:
        return DEFAULT_WEIGHTS.get(regime, {})

    @staticmethod
    def save_weights(weights: dict[Regime, dict[str, float]]):
        """保存权重到 state.json（self_optimizer --apply 时调用）"""
        _save_regime_weights(weights)

    @staticmethod
    def summarize_regime(regime: RegimeReport) -> str:
        conf = f"{regime.regime_confidence:.0%}"
        vol  = "⚠️高波动 " if regime.is_high_vol else ""
        mapping = {
            Regime.TRENDING_UP:   f"{vol}上涨趋势(置信{conf})",
            Regime.TRENDING_DOWN: f"{vol}下跌趋势(置信{conf})",
            Regime.RANGING:       f"{vol}震荡横盘",
        }
        return mapping.get(regime.regime, f"{vol}未知")
