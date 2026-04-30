"""
Realtime Monitor — 秒级持仓监控
Phase 4B: TP1/TP2/trailing-stop pyramid support
纯 polling 实现，零外部依赖
每 N 秒检查一次持仓价格，触发 TP/止损则处理
"""
import json
import threading
import time
from datetime import datetime, timezone, timedelta

try:
    from .config import MIN_VOLUME_M, TP1_CLOSE_PCT, TP2_CLOSE_PCT
    from .market import Market
    from .db.trades import TradeDB
    from .executor import Executor
    from .memory import Memory
except ImportError:
    from config import MIN_VOLUME_M, TP1_CLOSE_PCT, TP2_CLOSE_PCT
    from market import Market
    from db.trades import TradeDB
    from executor import Executor
    from memory import Memory


TZ_UTC8 = timezone(timedelta(hours=8))


def _now_str():
    return datetime.now(TZ_UTC8).strftime("%m-%d %H:%M")


class RealtimeMonitor:
    """
    持仓实时监控 — Phase 4B TP 金字塔：
    - polling 方式，每 interval 秒检查一次持仓价格
    - TP1 → 部分平仓 + 止损移至成本价
    - TP2 → 部分平仓 + 剩余走追踪止损
    - 追踪止损 → 平剩余全部
    - 硬止损 → 平剩余全部
    - 有新持仓自动加入监控
    """

    def __init__(self, interval: float = 1.0):
        self.interval = interval
        self._thread = None
        self._running = False
        self._last_prices = {}  # symbol -> price

    def _check_positions(self):
        """检查所有持仓是否触发 TP/止损"""
        positions = TradeDB.get_open()
        if not positions:
            return

        # 批量获取持仓价格
        tickers = Market.all_tickers()
        ticker_map = {t["symbol"]: float(t["lastPrice"]) for t in tickers}

        for pos in positions:
            symbol = pos["symbol"]
            price = ticker_map.get(symbol)
            if not price:
                continue

            # Update trailing stop if in profit (after TP1)
            new_trail = Executor.update_trailing_stop(pos, price)
            if new_trail:
                TradeDB.update(pos["id"], trailing_stop=new_trail)
                pos["trailing_stop"] = new_trail

            # Check TP levels / SL
            tp_actions = Executor.check_tp_levels(pos, price)
            if not tp_actions:
                continue

            action = tp_actions[0]
            action_type = action["type"]
            direction = pos["direction"]

            if action_type == "tp1":
                remaining = pos.get("remaining_pct", 100)
                pnl_usd = action["pnl_usd"]
                pnl_pct = action["pnl_pct"]
                remaining_pct = remaining - TP1_CLOSE_PCT

                TradeDB.partial_close(
                    pos["id"], price, _now_str(),
                    f"tp1_{TP1_CLOSE_PCT}%", pnl_pct, pnl_usd,
                    TP1_CLOSE_PCT, remaining_pct,
                    new_stop=pos["entry_price"]  # breakeven
                )

                pre_analysis = pos.get("pre_analysis") or {}
                if isinstance(pre_analysis, str):
                    try:
                        pre_analysis = json.loads(pre_analysis)
                    except json.JSONDecodeError:
                        pre_analysis = {}

                Memory.record_outcome(
                    pos["id"], symbol,
                    pre_analysis.get("type", ""),
                    direction, pnl_pct, pnl_usd, f"TP1+{TP1_CLOSE_PCT}%"
                )
                print(f"[MONITOR] TP1 {symbol} +{pnl_usd:.2f}U [{TP1_CLOSE_PCT}%] 剩余{remaining_pct}%")

            elif action_type == "tp2":
                remaining = pos.get("remaining_pct", 100)
                pnl_usd = action["pnl_usd"]
                pnl_pct = action["pnl_pct"]
                remaining_pct = max(remaining - TP2_CLOSE_PCT, 0)

                TradeDB.partial_close(
                    pos["id"], price, _now_str(),
                    f"tp2_{TP2_CLOSE_PCT}%", pnl_pct, pnl_usd,
                    TP2_CLOSE_PCT, remaining_pct
                )

                pre_analysis = pos.get("pre_analysis") or {}
                if isinstance(pre_analysis, str):
                    try:
                        pre_analysis = json.loads(pre_analysis)
                    except json.JSONDecodeError:
                        pre_analysis = {}

                Memory.record_outcome(
                    pos["id"], symbol,
                    pre_analysis.get("type", ""),
                    direction, pnl_pct, pnl_usd, f"TP2+{TP2_CLOSE_PCT}%"
                )
                print(f"[MONITOR] TP2 {symbol} +{pnl_usd:.2f}U [{TP2_CLOSE_PCT}%] 剩余{remaining_pct}%")

            elif action_type in ("trailing", "sl"):
                remaining = pos.get("remaining_pct", 100)
                pnl_usd = action["pnl_usd"]
                pnl_pct = action["pnl_pct"]
                reason = "追踪止损" if action_type == "trailing" else "止损"

                Executor.close_position(
                    pos["id"], price, reason,
                    round(pnl_pct, 2), pnl_usd
                )

                pre_analysis = pos.get("pre_analysis") or {}
                if isinstance(pre_analysis, str):
                    try:
                        pre_analysis = json.loads(pre_analysis)
                    except json.JSONDecodeError:
                        pre_analysis = {}

                Memory.record_outcome(
                    pos["id"], symbol,
                    pre_analysis.get("type", ""),
                    direction, pnl_pct, pnl_usd, reason
                )
                print(f"[MONITOR] 平仓 {symbol} {pnl_usd:+.2f}U [{reason}]")

    def _loop(self):
        """主循环"""
        print(f"[MONITOR] 启动 ({self.interval}s 间隔)")
        while self._running:
            try:
                self._check_positions()
            except Exception as e:
                print(f"[MONITOR ERROR] {e}")
            time.sleep(self.interval)
        print("[MONITOR] 已停止")

    def start(self):
        """启动监控线程"""
        if self._running:
            print("[MONITOR] 已经运行中")
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        """停止监控"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    @property
    def is_running(self) -> bool:
        return self._running
