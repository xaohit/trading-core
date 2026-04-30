"""
Backtest Data Layer — 历史K线 + 资金费率 + 24h行情
全量下载，存储到 SQLite，供回测长期复用
"""
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    from .config import PROXY
    from .market import Market
except ImportError:
    from config import PROXY
    from market import Market


TZ_UTC8 = timezone(timedelta(hours=8))
DATA_DIR = Path.home() / ".hermes" / "trading_core" / "backtest_data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

import sqlite3


def get_conn():
    db_path = DATA_DIR / "history.db"
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL 模式，支持高频写入
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_tables():
    conn = get_conn()
    c = conn.cursor()

    # K线表
    c.execute("""
        CREATE TABLE IF NOT EXISTS klines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            open_time INTEGER NOT NULL,
            open TEXT, high TEXT, low TEXT, close TEXT,
            volume TEXT, close_time INTEGER,
            quote_volume TEXT, n_trades INTEGER,
            taker_buy_volume TEXT,
            UNIQUE(symbol, timeframe, open_time)
        )
    """)

    # 资金费率表
    c.execute("""
        CREATE TABLE IF NOT EXISTS funding_rates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            funding_time INTEGER NOT NULL,
            rate REAL,
            UNIQUE(symbol, funding_time)
        )
    """)

    # 24h行情表
    c.execute("""
        CREATE TABLE IF NOT EXISTS tickers_24h (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            open_time INTEGER NOT NULL,
            last_price REAL,
            price_change REAL,
            price_change_pct REAL,
            volume REAL,
            quote_volume REAL,
            UNIQUE(symbol, open_time)
        )
    """)

    # 索引
    c.execute("CREATE INDEX IF NOT EXISTS idx_klines_sym_tf_ot ON klines(symbol, timeframe, open_time)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_funding_sym_ft ON funding_rates(symbol, funding_time)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ticker_sym_ot ON tickers_24h(symbol, open_time)")

    conn.commit()
    conn.close()
    print("[data] 表初始化完成")


# === K线下载 ===

def _klines_to_rows(symbol: str, timeframe: str, klines: list) -> list:
    """把币安K线格式转成数据库行"""
    rows = []
    for k in klines:
        rows.append((
            symbol, timeframe, int(k[0]),
            k[1], k[2], k[3], k[4], k[5], int(k[6]),
            k[7], int(k[8]), k[9]
        ))
    return rows


def download_klines(symbol: str, timeframe: str = "1h",
                    start_time: int = None, end_time: int = None,
                    limit: int = 1500):
    """
    下载K线，遍历下载直到 end_time
    end_time:毫秒时间戳，默认None=到现在
    返回总共下载了多少条
    """
    if end_time is None:
        end_time = int(time.time() * 1000)

    total = 0
    current_end = end_time

    while True:
        params = f"symbol={symbol}&interval={timeframe}&endTime={current_end}&limit={limit}"
        url = f"https://fapi.binance.com/fapi/v1/klines?{params}"
        cmd = [
            "curl", "-s", "--max-time", "15", "--proxy", PROXY,
            url
        ]
        import subprocess
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=20
            )
            if result.returncode != 0:
                break
            data = json.loads(result.stdout)
        except:
            break

        if not data:
            break

        rows = _klines_to_rows(symbol, timeframe, data)

        # 批量upsert
        conn = get_conn()
        c = conn.cursor()
        c.executemany("""
            INSERT OR IGNORE INTO klines
            (symbol, timeframe, open_time, open, high, low, close, volume, close_time, quote_volume, n_trades, taker_buy_volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)
        conn.commit()
        conn.close()

        downloaded = len(data)
        total += downloaded

        #  earliest open time in this batch
        earliest_ot = int(data[0][0])
        print(f"  {symbol} {timeframe}: +{downloaded}条, 最旧={datetime.fromtimestamp(earliest_ot/1000, tz=TZ_UTC8).strftime('%Y-%m-%d')}")

        if downloaded < limit:
            break

        # 翻页：继续往前查
        current_end = earliest_ot - 1

        # 安全限制，防止跑太久
        if start_time and earliest_ot <= start_time:
            break

        time.sleep(0.3)  # 防止触发限流

    return total


# === 资金费率下载 ===

def download_funding_rates(symbol: str, start_time: int = None, end_time: int = None):
    """
    下载资金费率历史（币安只保留最近90天的历史）
    start_time / end_time: 毫秒时间戳
    """
    if end_time is None:
        end_time = int(time.time() * 1000)
    if start_time is None:
        # 默认90天
        start_time = end_time - 90 * 24 * 3600 * 1000

    all_records = []
    current_start = start_time

    while True:
        url = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}"
        # 注意：币安没有历史资金费率API，只有当前值
        # 只能用这个endpoint循环采集
        cmd = [
            "curl", "-s", "--max-time", "15", "--proxy", PROXY,
            url
        ]
        import subprocess
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=20
            )
            if result.returncode != 0:
                break
            data = json.loads(result.stdout)
        except:
            break

        if not data or "symbol" not in data:
            break

        funding_time = int(time.time() * 1000)  # 用当前时间戳标记
        conn = get_conn()
        c = conn.cursor()
        c.execute("""
            INSERT OR IGNORE INTO funding_rates (symbol, funding_time, rate)
            VALUES (?, ?, ?)
        """, (symbol, funding_time, float(data.get("lastFundingRate", 0))))
        conn.commit()
        conn.close()

        all_records.append(float(data.get("lastFundingRate", 0)))
        time.sleep(0.3)

        # 资金费率8小时一次，每天3次，所以采集一次就够了（只有当前值）
        # 这里只是演示用，实际策略回测中每次scan时实时获取即可
        break  # 当前费率不需要循环

    return len(all_records)


# === 24h行情下载 ===

def download_ticker_24h(symbol: str = None):
    """
    下载24h行情快照
    如果传symbol则只下单个，否则下全部
    """
    if symbol:
        symbols = [symbol]
    else:
        # 获取所有合约 symbol
        tickers = Market.all_tickers()
        symbols = [t["symbol"] for t in tickers if t.get("symbol", "").endswith("USDT")]

    url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
    records = []

    for sym in symbols:
        cmd = [
            "curl", "-s", "--max-time", "15", "--proxy", PROXY,
            f"{url}?symbol={sym}"
        ]
        import subprocess
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=20
            )
            if result.returncode != 0:
                continue
            data = json.loads(result.stdout)
        except:
            continue

        if not data or "symbol" not in data:
            continue

        open_time = int(time.time() * 1000)
        conn = get_conn()
        c = conn.cursor()
        c.execute("""
            INSERT OR IGNORE INTO tickers_24h
            (symbol, open_time, last_price, price_change, price_change_pct, volume, quote_volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            sym,
            open_time,
            float(data.get("lastPrice", 0)),
            float(data.get("priceChange", 0)),
            float(data.get("priceChangePercent", 0)),
            float(data.get("volume", 0)),
            float(data.get("quoteVolume", 0)),
        ))
        conn.commit()
        conn.close()
        records.append(sym)
        time.sleep(0.1)

    return records


# === 全量下载主函数 ===

def download_all_klines(symbols: list = None,
                        timeframes: list = None,
                        days_back: int = 90):
    """
    全量下载K线
    symbols: 默认全市场USDT合约
    timeframes: 默认 ['1h', '4h', '1d']
    days_back: 往回查多少天（币安K线上限约90天）
    """
    if timeframes is None:
        timeframes = ["1h", "4h", "1d"]

    if symbols is None:
        tickers = Market.all_tickers()
        symbols = [t["symbol"] for t in tickers if t.get("symbol", "").endswith("USDT")]
        print(f"[data] 获取到 {len(symbols)} 个USDT合约")

    end_time = int(time.time() * 1000)
    start_time = end_time - days_back * 24 * 3600 * 1000

    total_symbols = len(symbols)
    for i, sym in enumerate(symbols):
        print(f"[{i+1}/{total_symbols}] 正在下载 {sym}")
        for tf in timeframes:
            n = download_klines(sym, tf, start_time, end_time)
            if n == 0:
                print(f"  {sym} {tf}: 无数据")
        time.sleep(0.2)

    print(f"[data] 全量K线下载完成，范围: {days_back}天")


# === 查询接口（供回测用）===

def get_klines(symbol: str, timeframe: str, limit: int = 1000) -> list:
    """读取本地K线，返回OHLCV列表"""
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT open_time, open, high, low, close, volume, quote_volume, n_trades
        FROM klines
        WHERE symbol=? AND timeframe=?
        ORDER BY open_time ASC
        LIMIT ?
    """, (symbol, timeframe, limit))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_funding_rate(symbol: str, nearest_time: int = None) -> float:
    """获取最近的资金费率"""
    conn = get_conn()
    c = conn.cursor()
    if nearest_time:
        c.execute("""
            SELECT rate FROM funding_rates
            WHERE symbol=? AND funding_time<=?
            ORDER BY funding_time DESC LIMIT 1
        """, (symbol, nearest_time))
    else:
        c.execute("""
            SELECT rate FROM funding_rates
            WHERE symbol=?
            ORDER BY funding_time DESC LIMIT 1
        """, (symbol,))
    row = c.fetchone()
    conn.close()
    return float(row["rate"]) if row else 0.0


def get_ticker_24h(symbol: str, limit: int = 100) -> list:
    """读取本地24h行情快照"""
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM tickers_24h
        WHERE symbol=?
        ORDER BY open_time DESC LIMIT ?
    """, (symbol, limit))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def symbol_coverage(symbol: str, timeframe: str) -> dict:
    """查看某币种K线覆盖范围"""
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT MIN(open_time) as start_ot, MAX(open_time) as end_ot, COUNT(*) as count
        FROM klines WHERE symbol=? AND timeframe=?
    """, (symbol, timeframe))
    row = c.fetchone()
    conn.close()
    if not row or row["count"] == 0:
        return {"start": None, "end": None, "count": 0}
    return {
        "start": datetime.fromtimestamp(row["start_ot"]/1000, tz=TZ_UTC8).strftime("%Y-%m-%d"),
        "end": datetime.fromtimestamp(row["end_ot"]/1000, tz=TZ_UTC8).strftime("%Y-%m-%d"),
        "count": row["count"]
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="历史数据下载")
    parser.add_argument("--symbols", nargs="*", help="指定币种，不传则全市场")
    parser.add_argument("--timeframes", nargs="*", default=["1h", "4h", "1d"], help="时间周期")
    parser.add_argument("--days", type=int, default=90, help="往回查天数")
    parser.add_argument("--check", action="store_true", help="检查已有数据覆盖范围")
    args = parser.parse_args()

    init_tables()

    if args.check:
        # 检查数据覆盖范围
        conn = get_conn()
        c = conn.cursor()
        c.execute("""
            SELECT symbol, timeframe, COUNT(*) as cnt,
                   MIN(open_time) as start_ot, MAX(open_time) as end_ot
            FROM klines GROUP BY symbol, timeframe
            ORDER BY end_ot DESC LIMIT 20
        """)
        for row in c.fetchall():
            start = datetime.fromtimestamp(row["start_ot"]/1000, tz=TZ_UTC8).strftime("%Y-%m-%d") if row["start_ot"] else "-"
            end = datetime.fromtimestamp(row["end_ot"]/1000, tz=TZ_UTC8).strftime("%Y-%m-%d") if row["end_ot"] else "-"
            print(f"  {row['symbol']} {row['timeframe']}: {row['cnt']}条 {start} ~ {end}")
        conn.close()
    else:
        download_all_klines(
            symbols=args.symbols,
            timeframes=args.timeframes,
            days_back=args.days
        )
