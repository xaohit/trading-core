"""
Risk Manager — 风控中枢
"""
try:
    from .config import (
        MAX_DAILY_LOSS_PCT, MAX_DRAWDOWN_PCT, MAX_OPEN_POSITIONS,
        POSITION_PCT, LEVERAGE, MIN_VOLUME_M, EXCLUDE_SYMBOLS,
        MAX_DAILY_TRADES, COOLDOWN_AFTER_LOSS_MINUTES, SECTOR_MAX_CONCENTRATION,
    )
    from .market import Market
    from .state import State
except ImportError:
    from config import (
        MAX_DAILY_LOSS_PCT, MAX_DRAWDOWN_PCT, MAX_OPEN_POSITIONS,
        POSITION_PCT, LEVERAGE, MIN_VOLUME_M, EXCLUDE_SYMBOLS,
        MAX_DAILY_TRADES, COOLDOWN_AFTER_LOSS_MINUTES, SECTOR_MAX_CONCENTRATION,
    )
    from market import Market
    from state import State


class RiskManager:
    """
    风控决策：
    - check_account_risk(): 账户级检查（日亏损、次数、持仓数、冷却、板块）
    - compute_position_size(): 风险反推算仓位
    - evaluate_entry_quality(): 入场质量7项评分
    """

    # 板块映射（借鉴 binance-square-monitor）
    SECTOR_MAP = {
        "majors": {"BTCUSDT", "ETHUSDT"},
        "l2": {"ARBUSDT", "OPUSDT", "STRKUSDT", "MATICUSDT", "MANAUSDT", "IMXUSDT"},
        "meme": {"DOGEUSDT", "SHIBUSDT", "PEPEUSDT", "WIFUSDT", "BONKUSDT", "FLOKIUSDT"},
        "ai": {"FETUSDT", "AGIXUSDT", "WLDUSDT", "OCEANUSDT", "RNDRUSDT", "NEARUSDT"},
        "defi": {"UNIUSDT", "AAVEUSDT", "CRVUSDT", "MKRUSDT", "SNXUSDT"},
        "alt_l1": {"SOLUSDT", "AVAXUSDT", "ADAUSDT", "DOTUSDT", "ATOMUSDT", "NEARUSDT", "APTUSDT", "SUIUSDT"},
    }

    def __init__(self, state: State = None):
        self.state = state or State()

    def check_account_risk(self, symbol: str, balance: float = None) -> tuple:
        """
        返回 (allowed: bool, reason: str)
        按顺序检查，所有检查独立
        """
        if balance is None:
            balance = Market.balance()

        # 1. 日亏损熔断
        if self.state.daily_loss_limit(max_loss_pct=MAX_DAILY_LOSS_PCT, balance=balance):
            return False, f"日亏损超限({MAX_DAILY_LOSS_PCT}%)"

        # 2. 日交易次数
        daily = self.state.daily
        today_trades = daily.get("trades", 0)
        if today_trades >= MAX_DAILY_TRADES:
            return False, f"日交易次数超限({today_trades}/{MAX_DAILY_TRADES})"

        # 3. 最大持仓数
        try:
            from .db.trades import TradeDB
        except ImportError:
            from db.trades import TradeDB
        open_positions = TradeDB.get_open()
        if len(open_positions) >= MAX_OPEN_POSITIONS:
            return False, f"持仓数已满({len(open_positions)}/{MAX_OPEN_POSITIONS})"

        # 4. 同token止损冷却
        cooldown_hours = COOLDOWN_AFTER_LOSS_MINUTES / 60.0
        if self.state.is_cooling(symbol, hours=cooldown_hours):
            return False, f"{symbol}止损冷却中({COOLDOWN_AFTER_LOSS_MINUTES}min)"

        # 5. 板块集中度
        sector = self._get_sector(symbol)
        sector_holdings = sum(
            1 for t in open_positions if self._get_sector(t["symbol"]) == sector
        )
        if sector_holdings >= SECTOR_MAX_CONCENTRATION:
            return False, f"板块{sector}集中度超限({sector_holdings}/{SECTOR_MAX_CONCENTRATION})"

        return True, "pass"

    def _get_sector(self, symbol: str) -> str:
        for sector, members in self.SECTOR_MAP.items():
            if symbol in members:
                return sector
        return "other"

    def compute_position_size(self, entry_price: float, stop_price: float,
                              balance: float = None, risk_pct: float = 2.0) -> dict:
        """
        风险反推算仓位
        risk_pct: 每笔交易愿意冒的风险比例（%）
        返回 {qty, position_usd, notional_usd, risk_amount}
        """
        if balance is None:
            balance = Market.balance()

        risk_amount = balance * risk_pct / 100
        stop_distance_pct = abs(entry_price - stop_price) / entry_price
        if stop_distance_pct == 0:
            stop_distance_pct = 0.02  # 默认2%

        qty = risk_amount / stop_distance_pct / entry_price
        position_usd = qty * entry_price
        notional_usd = position_usd * LEVERAGE

        # 上限约束
        max_notional = balance * POSITION_PCT / 100 * LEVERAGE
        if notional_usd > max_notional:
            qty = max_notional / LEVERAGE / entry_price
            position_usd = qty * entry_price
            notional_usd = position_usd * LEVERAGE

        return {
            "qty": round(qty, 6),
            "position_usd": round(position_usd, 4),
            "notional_usd": round(notional_usd, 4),
            "risk_amount": round(risk_amount, 4),
            "leverage": LEVERAGE,
        }

    def evaluate_entry_quality(self, symbol: str, signal: dict,
                                market_data: dict) -> tuple:
        """
        入场质量7项评分
        返回 (quality: str, passed_count: int, notes: list)
        quality: "FULL" | "HALF" | "SKIP"
        """
        passed = []
        failed = []

        # 1. verdict健康（用市场数据综合分）
        score = market_data.get("score", 50)
        if score >= 55:
            passed.append("综合分≥55")
        else:
            failed.append(f"综合分{score}偏低")

        # 2. 15m涨幅在合理范围
        change_15m = market_data.get("change_15m", 0)
        if -1.5 <= change_15m <= 2.0:
            passed.append("15m涨幅合理")
        else:
            failed.append(f"15m涨幅{change_15m}%超出范围")

        # 3. 1h涨幅合理（做多要求正向，做空要求负向）
        change_1h = market_data.get("change_1h", 0)
        direction = signal["direction"]
        if direction == "long" and change_1h >= 0:
            passed.append("1h正向")
        elif direction == "short" and change_1h <= 0:
            passed.append("1h负向")
        else:
            failed.append(f"1h走势与方向不匹配{change_1h}%")

        # 4. OI 15m增加
        oi_15m_chg = market_data.get("oi_15m_change", 0)
        if oi_15m_chg > 0:
            passed.append("OI_15m增加")
        else:
            failed.append(f"OI_15m下降{oi_15m_chg}%")

        # 5. OI 1h增加
        oi_1h_chg = market_data.get("oi_1h_change", 0)
        if oi_1h_chg > 0:
            passed.append("OI_1h增加")
        else:
            failed.append(f"OI_1h下降{oi_1h_chg}%")

        # 6. 主动买卖比合理
        taker_ratio = market_data.get("taker_ratio", 1.0)
        if 0.7 <= taker_ratio <= 1.5:
            passed.append("主动买卖比正常")
        else:
            failed.append(f"主动买卖比{taker_ratio}异常")

        # 7. 资金费率方向一致
        funding_rate = signal.get("funding_rate", 0)
        if direction == "long" and funding_rate < 0.03:
            passed.append("费率未过热")
        elif direction == "short" and funding_rate > -0.03:
            passed.append("费率未过冷")
        elif funding_rate == 0:
            passed.append("费率中性")
        else:
            failed.append(f"费率{funding_rate}%与方向冲突")

        passed_count = len(passed)
        signal_score = score

        if passed_count >= 6 and signal_score >= 65:
            quality = "FULL"
        elif passed_count >= 4 and signal_score >= 50:
            quality = "HALF"
        else:
            quality = "SKIP"

        notes = passed + [f"FAIL: {x}" for x in failed]
        return quality, passed_count, notes

    def filter_candidates(self, tickers: list) -> list:
        """预过滤候选币"""
        return [
            t for t in tickers
            if t["symbol"].endswith("USDT")
            and t["symbol"] not in EXCLUDE_SYMBOLS
            and float(t.get("quoteVolume", 0)) > MIN_VOLUME_M * 1e6
        ]
