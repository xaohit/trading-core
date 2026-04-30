"""
Configuration — 所有配置集中在这里
"""
import os
from pathlib import Path

# Load .env manually
env_path = Path.home() / ".hermes" / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

# ===== Binance API =====
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")

# ===== Proxy =====
PROXY = os.getenv("PROXY", "socks5h://localhost:7897")
PROXIES = {"https": PROXY, "http": PROXY}

# ===== Trading Params =====
MAX_OPEN_POSITIONS = 3
POSITION_PCT = 30          # 每笔仓位占总资金百分比
LEVERAGE = 3               # 杠杆倍数
COOLDOWN_HOURS = 4         # 同一币种开仓冷却（小时）
MIN_VOLUME_M = 20          # 最小24h成交量（USDT M）
MAX_POSITION_PCT = 50      # 单币最大仓位占比

# ===== Risk =====
MAX_DAILY_LOSS_PCT = 5     # 单日最大亏损（占总资金%）
MAX_DRAWDOWN_PCT = 10      # 最大回撤%
MAX_DAILY_TRADES = 15      # 每日最大开仓次数
COOLDOWN_AFTER_LOSS_MINUTES = 30  # 止损后冷却时间（分钟）
SECTOR_MAX_CONCENTRATION = 2      # 同板块最大持仓数
ENTRY_QUALITY_MIN_PASSED = 4      # 入场质量最少通过项数
ENTRY_QUALITY_MIN_SCORE = 50      # 入场质量最低综合分

# ===== ATR Risk Sizing & TP Pyramid =====
ATR_STOP_MULTIPLIER = 1.5       # 止损距离 = ATR% * multiplier
RISK_PER_TRADE_PCT = 2.0        # 每笔风险占权益%
TP1_R_MULTIPLE = 1.5            # TP1 在 1.5R 处（平 30%）
TP2_R_MULTIPLE = 3.0            # TP2 在 3R 处（平 30%）
TP1_CLOSE_PCT = 30              # TP1 平仓比例%
TP2_CLOSE_PCT = 30              # TP2 平仓比例%
TRAILING_STOP_ATR_MULT = 2.0    # 追踪止损距离 = 峰值 - ATR% * mult
ATR_LOOKBACK = 14               # ATR 计算周期
MIN_NOTIONAL_USDT = 5.0         # Binance 最小名义价值

# ===== Paths =====
BASE_DIR = Path.home() / ".hermes" / "trading_core"
DB_PATH = BASE_DIR / "trading_core.db"
HISTORY_DIR = BASE_DIR / "history"
STATE_PATH = BASE_DIR / "state.json"

# ===== Notifications =====
TG_TOKEN = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")

# ===== Scoring thresholds =====
ENV_MIN_SCORE = 3          # 环境检查最低得分
MIN_FUNDING_RATE = 0.03   # 资金费率阈值（%）
MIN_CRASH_PCT = -5        # 暴跌阈值（%）
MIN_PUMP_PCT = 5          # 暴涨阈值（%）
MIN_BOUNCE_PCT = 5        # 反弹最小（%）
MIN_PULLBACK_PCT = 10     # 回落最小（%）

# Excluded symbols
EXCLUDE_SYMBOLS = {
    "BTCUSDT", "ETHUSDT", "USDCUSDT", "FDUSDUSDT",
    "BTCDOMUSDT", "BTCSTUSDT", "BNBUSDT",
}

# ===== Strategy configs (defaults) =====
_DEFAULT_STRATEGY_CONFIGS = {
    "neg_funding_long": {
        "min_rate": -0.03,
        "sl_pct": 0.05,
        "tp_pct": 0.10,
        "min_change": -2,
    },
    "pos_funding_short": {
        "min_rate": 0.03,
        "sl_pct": 0.05,
        "tp_pct": 0.10,
        "min_change": 2,
    },
    "crash_bounce_long": {
        "min_crash": -5,
        "sl_pct": 0.03,
        "tp_pct": 0.08,
        "min_bounce": 5,
    },
    "pump_short": {
        "min_pump": 5,
        "sl_pct": 0.15,
        "tp_pct": 0.20,
        "min_pullback": 10,
    },
}

STRATEGY_CONFIGS = _DEFAULT_STRATEGY_CONFIGS  # 启动时默认


def get_strategy_config(signal_type: str) -> dict:
    """动态获取策略配置：优先用演化后的参数，否则用默认值"""
    from pathlib import Path
    import json
    state_path = Path.home() / ".hermes" / "trading_core" / "state.json"
    if state_path.exists():
        try:
            with open(state_path) as f:
                state = json.load(f)
            evolved = state.get("evolved_params", {})
            if signal_type in evolved:
                return evolved[signal_type]
        except Exception:
            pass
    return _DEFAULT_STRATEGY_CONFIGS.get(signal_type, {})


def reload_strategy_configs():
    """演化后重新加载配置"""
    global STRATEGY_CONFIGS
    from pathlib import Path
    import json
    state_path = Path.home() / ".hermes" / "trading_core" / "state.json"
    if state_path.exists():
        try:
            with open(state_path) as f:
                state = json.load(f)
            evolved = state.get("evolved_params", {})
            if evolved:
                STRATEGY_CONFIGS = {**_DEFAULT_STRATEGY_CONFIGS, **evolved}
                return
        except Exception:
            pass
    STRATEGY_CONFIGS = _DEFAULT_STRATEGY_CONFIGS

# ===== Strength thresholds =====
STRENGTH_S = {"neg_funding": -0.10, "pos_funding": 0.10, "crash": -20, "pump": 80}
STRENGTH_A = {"neg_funding": -0.05, "pos_funding": 0.05, "crash": -10, "pump": 30}

# ===== Decision Memory Loop =====
DECISION_MEMORY_ENABLED = os.getenv("DECISION_MEMORY_ENABLED", "1") == "1"
DECISION_REVIEW_HORIZON_HOURS = int(os.getenv("DECISION_REVIEW_HORIZON_HOURS", "24"))
DECISION_JOURNAL_ACTIONS = {
    action.strip()
    for action in os.getenv(
        "DECISION_JOURNAL_ACTIONS",
        "opened,score_reject,risk_reject,env_reject",
    ).split(",")
    if action.strip()
}

# ===== Social Heat (Phase 5) =====
SOCIAL_HEAT_ENABLED = os.getenv("SOCIAL_HEAT_ENABLED", "1") == "1"
HEAT_WINDOW_MINUTES = int(os.getenv("HEAT_WINDOW_MINUTES", "15"))
HEAT_HALF_LIFE_HOURS = float(os.getenv("HEAT_HALF_LIFE_HOURS", "0.25"))
HEAT_TOP_N = int(os.getenv("HEAT_TOP_N", "20"))
HEAT_CANDIDATE_N = int(os.getenv("HEAT_CANDIDATE_N", "15"))
