"""
Trade persistence layer
"""
import json
from datetime import datetime
from .connection import get_db

class TradeDB:
    @staticmethod
    def insert(trade: dict) -> int:
        conn = get_db()
        c = conn.cursor()
        c.execute('''
            INSERT INTO trades (symbol, direction, leverage, position_pct, position_usd,
            notional_usd, entry_price, stop_loss, take_profit, entry_time, status,
            pre_analysis, tp1_price, tp1_done, tp2_price, tp2_done, trailing_stop,
            remaining_pct, breakeven_done, initial_r, stop_distance, atr_pct_at_entry)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            trade["symbol"], trade["direction"], trade["leverage"],
            trade["position_pct"], trade["position_usd"], trade["notional_usd"],
            trade["entry_price"], trade["stop_loss"], trade["take_profit"],
            trade["entry_time"], "open", json.dumps(trade.get("pre_analysis", {})),
            trade.get("tp1_price"), trade.get("tp1_done", 0),
            trade.get("tp2_price"), trade.get("tp2_done", 0),
            trade.get("trailing_stop"), trade.get("remaining_pct", 100),
            trade.get("breakeven_done", 0), trade.get("initial_r"),
            trade.get("stop_distance"), trade.get("atr_pct_at_entry"),
        ))
        conn.commit()
        return c.lastrowid

    @staticmethod
    def update(trade_id: int, **fields):
        conn = get_db()
        c = conn.cursor()
        for key, val in fields.items():
            c.execute(f'UPDATE trades SET {key}=? WHERE id=?', (val, trade_id))
        conn.commit()

    @staticmethod
    def close(trade_id: int, exit_price: float, exit_time: str,
              exit_reason: str, pnl_pct: float, pnl_usd: float):
        conn = get_db()
        c = conn.cursor()
        c.execute('''
            UPDATE trades SET exit_price=?, exit_time=?, exit_reason=?,
            pnl_pct=?, pnl_usd=?, status='closed', remaining_pct=0 WHERE id=?
        ''', (exit_price, exit_time, exit_reason, pnl_pct, pnl_usd, trade_id))
        conn.commit()

    @staticmethod
    def partial_close(trade_id: int, exit_price: float, exit_time: str,
                      exit_reason: str, pnl_pct: float, pnl_usd: float,
                      close_pct: float, new_remaining: int,
                      new_stop: float = None, new_trailing: float = None):
        conn = get_db()
        c = conn.cursor()
        updates = ["remaining_pct=?"]
        params = [new_remaining]
        if new_stop is not None:
            updates.append("stop_loss=?")
            params.append(new_stop)
        if new_trailing is not None:
            updates.append("trailing_stop=?")
            params.append(new_trailing)
        if exit_reason.startswith("tp1"):
            updates.append("tp1_done=1")
        elif exit_reason.startswith("tp2"):
            updates.append("tp2_done=1")
        params.append(trade_id)
        params_str = ",".join(updates)
        c.execute(f'UPDATE trades SET {params_str} WHERE id=?', params)
        conn.commit()

    @staticmethod
    def get_closed_count() -> int:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as n FROM trades WHERE status='closed'")
        return c.fetchone()["n"] or 0

    @staticmethod
    def get_open():
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM trades WHERE status='open'")
        return [dict(row) for row in c.fetchall()]

    @staticmethod
    def get_all(limit=100):
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(row) for row in c.fetchall()]

    @staticmethod
    def get_closed(limit=50):
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM trades WHERE status='closed' ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(row) for row in c.fetchall()]

    @staticmethod
    def stats():
        conn = get_db()
        c = conn.cursor()
        c.execute("""
            SELECT COUNT(*) as total, SUM(pnl_pct) as pnl_pct,
                   SUM(pnl_usd) as pnl_usd,
                   SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins
            FROM trades WHERE status='closed'
        """)
        row = c.fetchone()
        if not row or row["total"] == 0:
            return {"total": 0, "wins": 0, "win_rate": 0, "pnl_pct": 0, "pnl_usd": 0}
        total = row["total"] or 0
        wins = row["wins"] or 0
        return {
            "total": total,
            "wins": wins,
            "win_rate": wins / total * 100 if total > 0 else 0,
            "pnl_pct": row["pnl_pct"] or 0,
            "pnl_usd": row["pnl_usd"] or 0,
        }

    @staticmethod
    def record_signal(scanned_at: str, symbol: str, signal: dict,
                      score: int, action: str, result: str = None,
                      snapshot: dict = None, analysis: dict = None):
        conn = get_db()
        c = conn.cursor()
        c.execute('''
            INSERT INTO signal_history
            (scanned_at, symbol, signal_type, strength, direction, price,
             funding_rate, change_24h, score, action, result, verdict, tags,
             notes, snapshot_json, analysis_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            scanned_at, symbol, signal.get("type"),
            signal.get("strength"), signal.get("direction"),
            signal.get("price"), signal.get("funding_rate"),
            signal.get("change_24h"), score, action, result,
            (analysis or {}).get("verdict") or signal.get("verdict"),
            json.dumps((analysis or {}).get("tags", signal.get("tags", [])), ensure_ascii=False),
            json.dumps((analysis or {}).get("notes", signal.get("notes", [])), ensure_ascii=False),
            json.dumps(snapshot or signal.get("snapshot", {}), ensure_ascii=False),
            json.dumps(analysis or signal.get("analysis", {}), ensure_ascii=False),
        ))
        conn.commit()

    @staticmethod
    def get_recent_signals(limit: int = 20):
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM signal_history ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(row) for row in c.fetchall()]
