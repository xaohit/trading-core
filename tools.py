"""
MCP Tools — 供其他 AI Agent 调用的工具接口
所有工具都是无参数的 () -> dict 形式，方便 MCP server 或 delegate_task 调用
"""
import sys
from pathlib import Path

# 确保可以 import
sys.path.insert(0, str(Path(__file__).parent))

from .db.connection import init_db
from .scanner import Scanner
from .db.trades import TradeDB
from .memory import Memory
from .market import Market
from .state import State


def scan_and_trade() -> dict:
    """
    [MCP工具] 运行完整扫描 + 自动交易
    监控持仓 → 检测信号 → 风控检查 → 开仓
    返回执行结果摘要
    """
    init_db()
    scanner = Scanner()
    result = scanner.run()
    return {
        "status": "ok",
        "closed_count": len(result.get("closed", [])),
        "closed": result.get("closed", []),
        "scan_action": result.get("scan", {}).get("action"),
        "opened": result.get("scan", {}).get("opened", 0),
        "best_signal": _summarize_signal(result.get("scan", {}).get("signal")),
        "timestamp": result.get("timestamp"),
    }


def monitor_positions() -> dict:
    """
    [MCP工具] 只监控持仓，不扫描新币
    检查所有持仓的止损止盈，触发则平仓
    """
    init_db()
    scanner = Scanner()
    closed = scanner.monitor()
    return {
        "status": "ok",
        "closed_count": len(closed),
        "closed": closed,
    }


def get_open_positions() -> dict:
    """
    [MCP工具] 获取当前持仓
    """
    init_db()
    positions = TradeDB.get_open()
    return {
        "status": "ok",
        "count": len(positions),
        "positions": positions,
    }


def get_performance_stats() -> dict:
    """
    [MCP工具] 获取交易统计数据
    """
    init_db()
    stats = TradeDB.stats()
    strategy_stats = Memory.get_strategy_stats()
    state = State()
    return {
        "status": "ok",
        "total_trades": stats["total"],
        "win_rate": f"{stats['win_rate']:.1f}%",
        "total_pnl_pct": f"{stats['pnl_pct']:+.1f}%",
        "total_pnl_usd": f"{stats['pnl_usd']:+.2f}U",
        "strategy_stats": strategy_stats,
        "daily": state.daily,
        "open_positions": len(TradeDB.get_open()),
    }


def get_market_scan(symbol: str = None) -> dict:
    """
    [MCP工具] 获取市场数据摘要
    symbol为空则返回全部候选币
    """
    tickers = Market.all_tickers()
    funding_rates = Market.funding_rates()

    if symbol:
        ticker = Market.ticker(symbol)
        if ticker:
            return {
                "status": "ok",
                "symbol": symbol,
                "price": float(ticker["lastPrice"]),
                "change_24h": float(ticker["priceChangePercent"]),
                "volume_24h": float(ticker.get("quoteVolume", 0)) / 1e6,
                "funding_rate": funding_rates.get(symbol, 0),
                "klines_1h": Market.klines(symbol, "1h", 24),
            }
        return {"status": "error", "symbol": symbol}

    # 返回前20个候选
    candidates = [
        t for t in tickers
        if t["symbol"].endswith("USDT")
        and float(t.get("quoteVolume", 0)) > 20e6
    ]
    candidates.sort(key=lambda x: -float(x.get("quoteVolume", 0)))
    return {
        "status": "ok",
        "count": len(candidates),
        "top_candidates": [
            {
                "symbol": t["symbol"],
                "price": float(t["lastPrice"]),
                "change_24h": float(t["priceChangePercent"]),
                "volume_24h": float(t.get("quoteVolume", 0)) / 1e6,
                "funding_rate": funding_rates.get(t["symbol"], 0),
            }
            for t in candidates[:20]
        ],
    }


def force_close(symbol: str = None) -> dict:
    """
    [MCP工具] 强制平仓
    不指定symbol则平所有持仓
    """
    init_db()
    if symbol:
        positions = TradeDB.get_open()
        targets = [p for p in positions if p["symbol"] == symbol]
    else:
        targets = TradeDB.get_open()

    closed = []
    for pos in targets:
        from .executor import Executor
        Executor._close_by_market(pos)
        closed.append(pos["symbol"])

    return {
        "status": "ok",
        "closed": closed,
        "count": len(closed),
    }


def get_signal_history(symbol: str = None, limit: int = 20) -> dict:
    """
    [MCP工具] 获取最近信号历史
    """
    init_db()
    signals = Memory.get_recent_signals(symbol=symbol, limit=limit)
    return {
        "status": "ok",
        "count": len(signals),
        "signals": signals,
    }


def _summarize_signal(signal: dict) -> dict:
    """信号摘要"""
    if not signal:
        return {}
    return {
        "symbol": signal.get("symbol"),
        "type": signal.get("type"),
        "direction": signal.get("direction"),
        "strength": signal.get("strength"),
        "reason": signal.get("reason"),
        "price": signal.get("price"),
        "env_score": signal.get("env_score"),
    }


# ===== MCP Tool Definitions =====
# 这些定义用于 MCP server 暴露工具列表

TOOL_DEFINITIONS = [
    {
        "name": "trading_scan_and_trade",
        "description": "运行完整交易扫描：监控持仓 + 检测信号 + 风控检查 + 自动开仓。返回执行结果。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "trading_monitor",
        "description": "只监控持仓的止损止盈，不扫描新币。返回触发平仓列表。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "trading_positions",
        "description": "获取当前所有持仓。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "trading_stats",
        "description": "获取交易统计数据（胜率、各策略表现、每日统计）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "trading_market",
        "description": "获取市场数据（单个币种详情或候选币列表）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "币种符号，如 BTCUSDT"}
            },
        },
    },
    {
        "name": "trading_close",
        "description": "强制平仓（单个币种或全部）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "币种符号，不填则平所有"}
            },
        },
    },
    {
        "name": "trading_signals",
        "description": "获取最近信号历史（用于分析系统决策模式）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "limit": {"type": "integer", "default": 20}
            },
        },
    },
]

TOOLS = {
    "scan_and_trade": scan_and_trade,
    "monitor_positions": monitor_positions,
    "get_open_positions": get_open_positions,
    "get_performance_stats": get_performance_stats,
    "get_market_scan": get_market_scan,
    "force_close": force_close,
    "get_signal_history": get_signal_history,
}
