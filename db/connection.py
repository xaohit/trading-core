"""
Database connection and initialization
"""
import sqlite3
from pathlib import Path

try:
    from .config import BASE_DIR, DB_PATH, HISTORY_DIR
except ImportError:
    from config import BASE_DIR, DB_PATH, HISTORY_DIR

DB_PATH.parent.mkdir(parents=True, exist_ok=True)
HISTORY_DIR.mkdir(parents=True, exist_ok=True)

_connection = None

def get_db():
    """单例数据库连接"""
    global _connection
    if _connection is None:
        _connection = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _connection.row_factory = sqlite3.Row
    return _connection

def init_db():
    """初始化数据库表"""
    conn = get_db()
    c = conn.cursor()

    # K线数据
    c.execute('''
        CREATE TABLE IF NOT EXISTS klines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            interval TEXT NOT NULL,
            open_time INTEGER NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            close_time INTEGER NOT NULL,
            quote_volume REAL NOT NULL,
            trades INTEGER NOT NULL,
            UNIQUE(symbol, interval, open_time)
        )
    ''')

    # 交易记录
    c.execute('''
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            leverage INTEGER DEFAULT 3,
            position_pct INTEGER DEFAULT 30,
            position_usd REAL,
            notional_usd REAL,
            entry_price REAL NOT NULL,
            stop_loss REAL,
            take_profit REAL,
            entry_time TEXT,
            exit_price REAL,
            exit_time TEXT,
            exit_reason TEXT,
            pnl_pct REAL,
            pnl_usd REAL,
            status TEXT DEFAULT 'open',
            pre_analysis TEXT,
            post_review TEXT,
            created_at INTEGER DEFAULT (strftime('%s', 'now'))
        )
    ''')

    # 策略参数演化
    c.execute('''
        CREATE TABLE IF NOT EXISTS strategy_evolution (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            changed_at INTEGER DEFAULT (strftime('%s', 'now')),
            param TEXT,
            old_val TEXT,
            new_val TEXT,
            reason TEXT,
            result TEXT
        )
    ''')
    _ensure_column(c, "strategy_evolution", "signal_type", "TEXT")
    _ensure_column(c, "strategy_evolution", "outcome", "TEXT")
    _ensure_column(c, "strategy_evolution", "pnl_pct", "REAL")
    _ensure_column(c, "strategy_evolution", "exit_reason", "TEXT")
    _ensure_column(c, "strategy_evolution", "recorded_at", "INTEGER")

    # 每日统计
    c.execute('''
        CREATE TABLE IF NOT EXISTS daily_stats (
            date TEXT PRIMARY KEY,
            total_trades INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            pnl_pct REAL DEFAULT 0,
            pnl_usd REAL DEFAULT 0,
            open_count INTEGER DEFAULT 0
        )
    ''')

    # 信号历史（用于回测）
    c.execute('''
        CREATE TABLE IF NOT EXISTS signal_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scanned_at TEXT,
            symbol TEXT,
            signal_type TEXT,
            strength TEXT,
            direction TEXT,
            price REAL,
            funding_rate REAL,
            change_24h REAL,
            score INTEGER,
            action TEXT,
            result TEXT
        )
    ''')

    _ensure_column(c, "trades", "tp1_price", "REAL")
    _ensure_column(c, "trades", "tp1_done", "INTEGER DEFAULT 0")
    _ensure_column(c, "trades", "tp2_price", "REAL")
    _ensure_column(c, "trades", "tp2_done", "INTEGER DEFAULT 0")
    _ensure_column(c, "trades", "trailing_stop", "REAL")
    _ensure_column(c, "trades", "remaining_pct", "INTEGER DEFAULT 100")
    _ensure_column(c, "trades", "breakeven_done", "INTEGER DEFAULT 0")
    _ensure_column(c, "trades", "initial_r", "REAL")
    _ensure_column(c, "trades", "stop_distance", "REAL")
    _ensure_column(c, "trades", "atr_pct_at_entry", "REAL")

    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status, entry_time)"
    )

    _ensure_column(c, "signal_history", "verdict", "TEXT")
    _ensure_column(c, "signal_history", "tags", "TEXT")
    _ensure_column(c, "signal_history", "notes", "TEXT")
    _ensure_column(c, "signal_history", "snapshot_json", "TEXT")
    _ensure_column(c, "signal_history", "analysis_json", "TEXT")

    c.execute('''
        CREATE TABLE IF NOT EXISTS decision_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at INTEGER DEFAULT (strftime('%s', 'now')),
            due_at INTEGER,
            status TEXT DEFAULT 'pending',
            symbol TEXT NOT NULL,
            direction TEXT,
            action TEXT,
            signal_type TEXT,
            strength TEXT,
            conviction REAL,
            entry_price REAL,
            target_price REAL,
            invalid_price REAL,
            horizon_hours REAL DEFAULT 24,
            reasoning TEXT,
            tags TEXT,
            context_json TEXT,
            source_trade_id INTEGER,
            reviewed_at INTEGER,
            -- Phase 8A: Deep Context Fields
            macro_context TEXT,
            market_state TEXT,
            agent_reasoning TEXT
        )
    ''')

    _ensure_column(c, "decision_snapshots", "macro_context", "TEXT")
    _ensure_column(c, "decision_snapshots", "market_state", "TEXT")
    _ensure_column(c, "decision_snapshots", "agent_reasoning", "TEXT")

    c.execute('''
        CREATE TABLE IF NOT EXISTS decision_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER NOT NULL,
            reviewed_at INTEGER DEFAULT (strftime('%s', 'now')),
            review_price REAL,
            return_pct REAL,
            max_favorable_pct REAL,
            max_adverse_pct REAL,
            direction_correct INTEGER,
            target_hit INTEGER,
            invalidated INTEGER,
            outcome_label TEXT,
            outcome_json TEXT,
            UNIQUE(snapshot_id)
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS experience_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at INTEGER DEFAULT (strftime('%s', 'now')),
            source_snapshot_id INTEGER,
            symbol TEXT,
            signal_type TEXT,
            outcome_label TEXT,
            tags TEXT,
            lesson TEXT,
            adjustment_json TEXT,
            searchable_text TEXT
        )
    ''')

    c.execute("CREATE INDEX IF NOT EXISTS idx_decision_due ON decision_snapshots(status, due_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_experience_symbol ON experience_cases(symbol, signal_type)")

    c.execute('''
        CREATE TABLE IF NOT EXISTS failure_archive (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER,
            symbol TEXT NOT NULL,
            signal_type TEXT,
            direction TEXT,
            entry_price REAL,
            exit_price REAL,
            pnl_pct REAL,
            exit_reason TEXT,
            tags TEXT,
            entry_snapshot_json TEXT,
            exit_snapshot_json TEXT,
            archived_at INTEGER DEFAULT (strftime('%s', 'now'))
        )
    ''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_failure_symbol ON failure_archive(symbol, signal_type)")

    conn.commit()
    return conn


def _ensure_column(cursor, table: str, column: str, ddl: str):
    cursor.execute(f"PRAGMA table_info({table})")
    cols = {row["name"] for row in cursor.fetchall()}
    if column not in cols:
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" in str(exc).lower():
                return
            raise
