"""
Candle/Kline persistence layer
"""
from .connection import get_db

class CandleDB:
    @staticmethod
    def insert(symbol: str, interval: str, klines: list):
        """批量插入K线数据"""
        if not klines:
            return
        conn = get_db()
        c = conn.cursor()
        rows = []
        for k in klines:
            # Binance kline format: [open_time, open, high, low, close, volume, close_time, ...]
            rows.append((
                symbol, interval,
                int(k[0]), float(k[1]), float(k[2]),
                float(k[3]), float(k[4]), float(k[5]),
                int(k[6]), float(k[7]) if len(k) > 7 else 0,
                int(k[8]) if len(k) > 8 else 0,
            ))
        c.executemany('''
            INSERT OR IGNORE INTO klines
            (symbol, interval, open_time, open, high, low, close, volume,
             close_time, quote_volume, trades)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', rows)
        conn.commit()

    @staticmethod
    def get(symbol: str, interval: str, limit=100):
        conn = get_db()
        c = conn.cursor()
        c.execute('''
            SELECT * FROM klines
            WHERE symbol=? AND interval=?
            ORDER BY open_time DESC LIMIT ?
        ''', (symbol, interval, limit))
        return [dict(row) for row in c.fetchall()]

    @staticmethod
    def latest(symbol: str, interval: str):
        conn = get_db()
        c = conn.cursor()
        c.execute('''
            SELECT * FROM klines
            WHERE symbol=? AND interval=?
            ORDER BY open_time DESC LIMIT 1
        ''', (symbol, interval))
        row = c.fetchone()
        return dict(row) if row else None
