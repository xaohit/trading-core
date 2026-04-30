"""
Executor — 订单执行层
Phase 4B: ATR risk sizing + TP1/TP2/trailing-stop pyramid
"""
import json
import time
from datetime import datetime, timezone, timedelta

try:
    from .config import (
        BINANCE_API_KEY, BINANCE_API_SECRET, PROXY, PROXIES,
        TG_TOKEN, TG_CHAT_ID, LEVERAGE, POSITION_PCT,
        ATR_STOP_MULTIPLIER, RISK_PER_TRADE_PCT,
        TP1_R_MULTIPLE, TP2_R_MULTIPLE,
        TP1_CLOSE_PCT, TP2_CLOSE_PCT, TRAILING_STOP_ATR_MULT,
        MIN_NOTIONAL_USDT,
    )
    from .market import Market
    from .state import State
    from .market_snapshot import _atr_pct
    from .db.trades import TradeDB
except ImportError:
    from config import (
        BINANCE_API_KEY, BINANCE_API_SECRET, PROXY, PROXIES,
        TG_TOKEN, TG_CHAT_ID, LEVERAGE, POSITION_PCT,
        ATR_STOP_MULTIPLIER, RISK_PER_TRADE_PCT,
        TP1_R_MULTIPLE, TP2_R_MULTIPLE,
        TP1_CLOSE_PCT, TP2_CLOSE_PCT, TRAILING_STOP_ATR_MULT,
        MIN_NOTIONAL_USDT,
    )
    from market import Market
    from state import State
    from market_snapshot import _atr_pct
    from db.trades import TradeDB


TZ_UTC8 = timezone(timedelta(hours=8))


def _now_str():
    return datetime.now(TZ_UTC8).strftime("%m-%d %H:%M")


class Executor:
    """
    交易执行（Phase 4B — ATR 风控 + TP 金字塔）：
    - open_position(): ATR 止损 + TP1/TP2/追踪止盈开仓
    - check_tp_levels(): 检查是否触发 TP1/TP2/追踪止损
    - close_position(): 全平
    - close_all(): 清仓
    """

    # ------------------------------------------------------------------
    # Open position — ATR risk sizing + TP pyramid
    # ------------------------------------------------------------------

    @staticmethod
    def open_position(symbol: str, direction: str, signal: dict,
                      balance: float = None) -> dict:
        """
        开仓入口 — ATR 风险 sizing
        返回 trade dict 或 None
        """
        if balance is None:
            balance = Market.balance()

        price = signal.get("price", 0)
        if not price:
            ticker = Market.ticker(symbol)
            price = float(ticker["lastPrice"]) if ticker else 0
        if not price:
            return None

        # 1. ATR-based stop distance
        atr_p = _atr_pct(symbol)
        if atr_p is not None and atr_p > 0:
            stop_distance = atr_p / 100 * ATR_STOP_MULTIPLIER
        else:
            # Fallback to strategy sl_pct if ATR unavailable
            stop_distance = signal.get("sl_pct", 0.05)

        # Clamp stop distance to reasonable range
        stop_distance = max(0.01, min(stop_distance, 0.20))

        # 2. R-multiple based TP levels
        r_value = price * stop_distance
        tp1_price = price + r_value * TP1_R_MULTIPLE if direction == "long" else price - r_value * TP1_R_MULTIPLE
        tp2_price = price + r_value * TP2_R_MULTIPLE if direction == "long" else price - r_value * TP2_R_MULTIPLE
        sl_price = price - r_value if direction == "long" else price + r_value

        # Initial trailing stop = entry - ATR*trailing_mult (same side as SL but tighter)
        trail_dist = price * (atr_p / 100 * TRAILING_STOP_ATR_MULT) if atr_p else r_value * TRAILING_STOP_ATR_MULT
        trailing_stop = price - trail_dist if direction == "long" else price + trail_dist

        # 3. Risk-based position sizing
        risk_amount = balance * RISK_PER_TRADE_PCT / 100
        notional = risk_amount / stop_distance

        # Clamp by POSITION_PCT max and MIN_NOTIONAL
        max_notional = balance * POSITION_PCT / 100 * LEVERAGE
        notional = min(notional, max_notional)
        notional = max(notional, MIN_NOTIONAL_USDT)

        position_usd = notional / LEVERAGE
        qty = notional / price

        # 4. Build trade record
        trade = {
            "symbol": symbol,
            "direction": direction,
            "leverage": LEVERAGE,
            "position_pct": round(position_usd / balance * 100, 1) if balance > 0 else POSITION_PCT,
            "position_usd": round(position_usd, 4),
            "notional_usd": round(notional, 4),
            "entry_price": price,
            "stop_loss": round(sl_price, 6),
            "take_profit": round(tp2_price, 6),
            "entry_time": _now_str(),
            "tp1_price": round(tp1_price, 6),
            "tp1_done": 0,
            "tp2_price": round(tp2_price, 6),
            "tp2_done": 0,
            "trailing_stop": round(trailing_stop, 6),
            "remaining_pct": 100,
            "breakeven_done": 0,
            "initial_r": round(r_value, 8),
            "stop_distance": round(stop_distance, 6),
            "atr_pct_at_entry": round(atr_p, 4) if atr_p is not None else None,
            "pre_analysis": {
                "type": signal.get("type"),
                "strength": signal.get("strength"),
                "reason": signal.get("reason"),
                "sl_pct": signal.get("sl_pct"),
                "tp_pct": signal.get("tp_pct"),
                "atr_pct": round(atr_p, 4) if atr_p is not None else None,
                "stop_distance": round(stop_distance, 4),
                "r_value": round(r_value, 6),
                "tp1_price": round(tp1_price, 6),
                "tp2_price": round(tp2_price, 6),
                "env_score": signal.get("env_score"),
                "composite_score": signal.get("composite_score"),
                "verdict": signal.get("verdict"),
                "tags": signal.get("tags", []),
                "snapshot": signal.get("snapshot", {}),
                "analysis": signal.get("analysis", {}),
            }
        }

        trade_id = TradeDB.insert(trade)
        trade["id"] = trade_id

        # 记录冷却
        State().record_open(symbol, _now_str())

        # 通知
        Executor._notify(
            f"[开仓] #{trade_id}\n"
            f"币种: {symbol}\n"
            f"方向: {'做多' if direction=='long' else '做空'} {LEVERAGE}x\n"
            f"入场: {price}\n"
            f"止损: {sl_price:.6f} ({stop_distance*100:.2f}%)\n"
            f"TP1: {tp1_price:.6f} ({TP1_CLOSE_PCT}%)\n"
            f"TP2: {tp2_price:.6f} ({TP2_CLOSE_PCT}%)\n"
            f"追踪: {trailing_stop:.6f}\n"
            f"仓位: {position_usd:.2f}U (风险 {RISK_PER_TRADE_PCT}%)"
        )

        return trade

    # ------------------------------------------------------------------
    # TP pyramid checker — called by monitor each tick
    # ------------------------------------------------------------------

    @staticmethod
    def check_tp_levels(pos: dict, price: float) -> list:
        """
        检查当前价格是否触发 TP1/TP2/追踪止损。
        返回 action 列表，每个 action 为 dict:
          {"type": "tp1"|"tp2"|"trailing"|"sl", "pnl_pct": float, "pnl_usd": float}
        注意：每次调用只返回最高优先级的一个动作（避免重复触发）。
        """
        direction = pos["direction"]
        entry = pos["entry_price"]
        lev = pos["leverage"]
        remaining = pos.get("remaining_pct", 100)

        # TP1 check
        if not pos.get("tp1_done") and pos.get("tp1_price"):
            tp1 = pos["tp1_price"]
            if (direction == "long" and price >= tp1) or \
               (direction == "short" and price <= tp1):
                pnl_pct = Executor._pnl_pct(direction, entry, price, lev)
                pnl_usd = pnl_pct / 100 * pos.get("position_usd", 10) * (TP1_CLOSE_PCT / 100)
                return [{"type": "tp1", "pnl_pct": round(pnl_pct, 2), "pnl_usd": round(pnl_usd, 4)}]

        # TP2 check (only after TP1)
        if pos.get("tp1_done") and not pos.get("tp2_done") and pos.get("tp2_price"):
            tp2 = pos["tp2_price"]
            if (direction == "long" and price >= tp2) or \
               (direction == "short" and price <= tp2):
                pnl_pct = Executor._pnl_pct(direction, entry, price, lev)
                remaining_usd = pos.get("position_usd", 10) * (remaining / 100)
                pnl_usd = pnl_pct / 100 * remaining_usd * (TP2_CLOSE_PCT / max(remaining, 1))
                return [{"type": "tp2", "pnl_pct": round(pnl_pct, 2), "pnl_usd": round(pnl_usd, 4)}]

        # Trailing stop check (only after TP1, i.e. remaining <= 70%)
        if pos.get("tp1_done") and pos.get("trailing_stop"):
            ts = pos["trailing_stop"]
            if (direction == "long" and price <= ts) or \
               (direction == "short" and price >= ts):
                pnl_pct = Executor._pnl_pct(direction, entry, price, lev)
                remaining_usd = pos.get("position_usd", 10) * (remaining / 100)
                pnl_usd = pnl_pct / 100 * remaining_usd
                return [{"type": "trailing", "pnl_pct": round(pnl_pct, 2), "pnl_usd": round(pnl_usd, 4)}]

        # Hard stop loss
        sl = pos.get("stop_loss")
        if sl:
            if (direction == "long" and price <= sl) or \
               (direction == "short" and price >= sl):
                pnl_pct = Executor._pnl_pct(direction, entry, price, lev)
                remaining_usd = pos.get("position_usd", 10) * (remaining / 100)
                pnl_usd = pnl_pct / 100 * remaining_usd
                return [{"type": "sl", "pnl_pct": round(pnl_pct, 2), "pnl_usd": round(pnl_usd, 4)}]

        return []

    # ------------------------------------------------------------------
    # Trailing stop update
    # ------------------------------------------------------------------

    @staticmethod
    def update_trailing_stop(pos: dict, price: float) -> float | None:
        """
        根据当前峰值更新追踪止损价。
        返回新的 trailing_stop，若无需更新则返回 None。
        """
        if not pos.get("tp1_done") or not pos.get("trailing_stop"):
            return None

        direction = pos["direction"]
        current_trail = pos["trailing_stop"]
        atr_p = pos.get("atr_pct_at_entry")
        r_value = pos.get("initial_r", 0)

        if atr_p and atr_p > 0:
            trail_dist = price * (atr_p / 100 * TRAILING_STOP_ATR_MULT)
        elif r_value:
            trail_dist = r_value * TRAILING_STOP_ATR_MULT
        else:
            return None

        if direction == "long":
            new_trail = price - trail_dist
            if new_trail > current_trail:
                return round(new_trail, 6)
        else:
            new_trail = price + trail_dist
            if new_trail < current_trail:
                return round(new_trail, 6)

        return None

    # ------------------------------------------------------------------
    # Full close
    # ------------------------------------------------------------------

    @staticmethod
    def close_position(trade_id: int, exit_price: float,
                       exit_reason: str, pnl_pct: float, pnl_usd: float):
        """全平"""
        TradeDB.close(trade_id, exit_price, _now_str(),
                      exit_reason, pnl_pct, pnl_usd)
        State().record_trade(pnl_pct, pnl_usd)

        Executor._notify(
            f"[平仓] #{trade_id}\n"
            f"价格: {exit_price}\n"
            f"盈亏: {pnl_pct:+.2f}% ({pnl_usd:+.2f}U)\n"
            f"原因: {exit_reason}"
        )

    @staticmethod
    def close_all():
        """清仓所有持仓"""
        positions = TradeDB.get_open()
        for pos in positions:
            Executor._close_by_market(pos)
        return len(positions)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _pnl_pct(direction: str, entry: float, price: float, lev: int) -> float:
        if direction == "long":
            return (price - entry) / entry * 100 * lev
        return (entry - price) / entry * 100 * lev

    @staticmethod
    def _close_by_market(trade: dict):
        """按市价平仓（简化：直接记录当前价）"""
        symbol = trade["symbol"]
        ticker = Market.ticker(symbol)
        if not ticker:
            return
        price = float(ticker["lastPrice"])
        direction = trade["direction"]
        entry = trade["entry_price"]
        lev = trade["leverage"]
        remaining = trade.get("remaining_pct", 100)
        pos_usd = trade.get("position_usd", 10) * (remaining / 100)

        pnl_pct = Executor._pnl_pct(direction, entry, price, lev)
        pnl_usd = pnl_pct / 100 * pos_usd

        # Determine reason
        sl = trade.get("stop_loss")
        tp = trade.get("take_profit")
        if sl:
            if (direction == "long" and price <= sl) or \
               (direction == "short" and price >= sl):
                Executor.close_position(
                    trade["id"], price, "止损",
                    round(pnl_pct, 2), round(pnl_usd, 4)
                )
                return
        if tp:
            if (direction == "long" and price >= tp) or \
               (direction == "short" and price <= tp):
                Executor.close_position(
                    trade["id"], price, "止盈",
                    round(pnl_pct, 2), round(pnl_usd, 4)
                )
                return

        Executor.close_position(
            trade["id"], price, "手动平仓",
            round(pnl_pct, 2), round(pnl_usd, 4)
        )

    @staticmethod
    def _notify(msg: str):
        """Telegram通知"""
        if not TG_TOKEN or not TG_CHAT_ID:
            return
        try:
            import requests
            requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
                timeout=10, proxies=PROXIES
            )
        except:
            pass

    @staticmethod
    def log(msg: str):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
