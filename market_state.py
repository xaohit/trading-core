"""
Market State Classifier — DEPRECATED

⚠️  此文件仅作向后兼容保留。
真实数据来源已迁移至 market_regime.py。

所有调用已重定向到 market_regime.classify_market_state()，
后者内部委托给 MarketRegimeDetector。

请勿在此文件添加新代码。
"""

try:
    from market_regime import classify_market_state
except ImportError:
    from .market_regime import classify_market_state
