"""
market_monitor.py — Phase 1: 可靠市场监控
每5分钟运行一次，并发拉数据，永不hang
"""
import asyncio, time, traceback
from datetime import datetime
from typing import Optional
from .data_fetcher import fetch_all
from .signal_engine import find_signals


class MarketMonitor:
    """永远不挂的监控"""

    def __init__(self):
        pass

    async def run_once(self) -> Optional[dict]:
        """
        一次完整监控循环。
        任意步骤失败 → 记录 → 返回 None（不抛异常）
        """
        started = datetime.now()
        errors = []

        try:
            # 1. 并发拉全市场数据
            tickers, funding_rates, fear_greed = await fetch_all()
        except Exception as e:
            errors.append(f"fetcher: {e}")
            tickers, funding_rates, fear_greed = [], {}, None

        if not tickers:
            return {"ok": False, "errors": errors, "duration": 0}

        # 2. 信号筛选
        try:
            signals = find_signals(tickers, funding_rates, fear_greed)
        except Exception as e:
            errors.append(f"engine: {e}")
            signals = []

        duration = (datetime.now() - started).total_seconds()

        return {
            "ok": True,
            "time": started.isoformat(),
            "tickers_checked": len(tickers),
            "signals_found": len(signals),
            "signals": signals,
            "duration_s": round(duration, 1),
            "errors": errors,
        }
