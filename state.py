"""
State management — in-memory + persistent state
"""
import json
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional

try:
    from .config import STATE_PATH
except ImportError:
    from config import STATE_PATH

TZ_UTC8 = timezone(timedelta(hours=8))

STATE_PATH = Path.home() / ".hermes" / "trading_core" / "state.json"


class State:
    """
    全局状态管理：
    - last_opens: {symbol: timestamp} 冷却追踪
    - stats: 全局统计
    - daily_pnl: 当日盈亏
    - daily_trades: 当日交易数
    """

    def __init__(self):
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    def _load(self) -> dict:
        if STATE_PATH.exists():
            try:
                with open(STATE_PATH) as f:
                    return json.load(f)
            except:
                pass
        return self._default()

    def _default(self) -> dict:
        return {
            "last_opens": {},      # {symbol: "MM-DD HH:MM"}
            "stats": {"total": 0, "wins": 0, "losses": 0, "pnl": 0},
            "daily": {
                "date": "",
                "pnl": 0,
                "trades": 0,
                "losses": 0,
            }
        }

    def save(self):
        with open(STATE_PATH, "w") as f:
            json.dump(self._data, f, indent=2)

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value):
        self._data[key] = value

    def is_cooling(self, symbol: str, hours: int = 4) -> bool:
        """检查币种是否在冷却中"""
        last = self._data.get("last_opens", {}).get(symbol)
        if not last:
            return False
        try:
            from datetime import datetime, timezone, timedelta
            TZ_UTC8 = timezone(timedelta(hours=8))
            dt = datetime.strptime(last, "%m-%d %H:%M").replace(tzinfo=TZ_UTC8)
            elapsed = (time.time() - dt.timestamp())
            return elapsed < hours * 3600
        except:
            return False

    def record_open(self, symbol: str, time_str: str = None):
        if time_str is None:
            from datetime import datetime, timezone, timedelta
            TZ_UTC8 = timezone(timedelta(hours=8))
            time_str = datetime.now(TZ_UTC8).strftime("%m-%d %H:%M")
        self._data.setdefault("last_opens", {})[symbol] = time_str
        self.save()

    def record_trade(self, pnl_pct: float, pnl_usd: float):
        """记录已平仓交易，更新统计"""
        self._data["stats"]["total"] += 1
        if pnl_pct > 0:
            self._data["stats"]["wins"] += 1
        else:
            self._data["stats"]["losses"] += 1
        self._data["stats"]["pnl"] = self._data["stats"].get("pnl", 0) + pnl_usd

        # daily
        from datetime import datetime, timezone, timedelta
        today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
        daily = self._data.setdefault("daily", {})
        if daily.get("date") != today:
            daily["date"] = today
            daily["pnl"] = 0
            daily["trades"] = 0
            daily["losses"] = 0
        daily["pnl"] += pnl_usd
        daily["trades"] += 1
        if pnl_pct < 0:
            daily["losses"] += 1
        self.save()

    def daily_loss_limit(self, max_loss_pct: float = 5, balance: float = 40) -> bool:
        """当日亏损是否超限：只有净亏损达到阈值才熔断，盈利不触发"""
        daily = self._data.get("daily", {})
        if daily.get("date") != datetime.now(TZ_UTC8).strftime("%Y-%m-%d"):
            return False
        pnl = daily.get("pnl", 0) or 0
        return pnl <= -(balance * max_loss_pct / 100)

    def clear_cooldown(self, symbol: str):
        if symbol in self._data.get("last_opens", {}):
            del self._data["last_opens"][symbol]
            self.save()

    @property
    def stats(self) -> dict:
        return self._data.get("stats", {})

    @property
    def daily(self) -> dict:
        return self._data.get("daily", {})
