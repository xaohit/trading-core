"""
Paper Balance — 模拟仓余额/权益追踪
余额由交易历史自行推导，不依赖 Binance API
"""
import json
import threading
from pathlib import Path

try:
    from .db.trades import TradeDB
    from .market import Market
except ImportError:
    from db.trades import TradeDB
    from market import Market


class PaperBalance:
    """
    模拟仓余额追踪器（线程安全）
    equity = initial_capital + closed_pnl + unrealized_pnl
    """
    LOCK = threading.Lock()
    INITIAL_CAPITAL = 40.0

    @classmethod
    def get(cls) -> dict:
        """返回 {balance, equity, closed_pnl, unrealized_pnl, initial_capital}"""
        with cls.LOCK:
            open_positions = TradeDB.get_open()
            closed_trades = TradeDB.get_closed(9999)

            closed_pnl = sum(t.get("pnl_usd", 0) or 0 for t in closed_trades)

            unrealized_pnl = 0.0
            tickers = Market.all_tickers()
            ticker_map = {t["symbol"]: float(t["lastPrice"]) for t in tickers}

            for pos in open_positions:
                sym = pos["symbol"]
                price = ticker_map.get(sym)
                if not price:
                    continue
                direction = pos["direction"]
                entry = pos["entry_price"]
                lev = pos["leverage"]
                remaining = pos.get("remaining_pct", 100)
                pos_usd = pos.get("position_usd", 10) * (remaining / 100)
                if direction == "long":
                    pnl_pct = (price - entry) / entry * 100 * lev
                else:
                    pnl_pct = (entry - price) / entry * 100 * lev
                unrealized_pnl += pnl_pct / 100 * pos_usd

            balance = cls.INITIAL_CAPITAL + closed_pnl
            equity = balance + unrealized_pnl

            return {
                "initial_capital": cls.INITIAL_CAPITAL,
                "balance": round(balance, 4),
                "equity": round(equity, 4),
                "closed_pnl": round(closed_pnl, 4),
                "unrealized_pnl": round(unrealized_pnl, 4),
            }

    @classmethod
    def equity_curve(cls, history: list = None) -> list:
        """从历史交易重建权益曲线"""
        with cls.LOCK:
            if history is None:
                history = TradeDB.get_closed(9999)

            curve = [cls.INITIAL_CAPITAL]
            running = cls.INITIAL_CAPITAL
            for h in sorted(history, key=lambda x: x.get("id", 0)):
                pnl = h.get("pnl_usd", 0) or 0
                running += pnl
                curve.append(round(running, 4))
            return curve
