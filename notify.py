"""
Notification Layer
- daemon 写事件到 notifications.json
- HermesWatcher 监听文件变化 → send_message 推给用户（秒级）
"""
import json
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

NOTIFY_PATH = Path.home() / ".hermes" / "trading_core" / "notifications.json"
NOTIFY_PATH.parent.mkdir(parents=True, exist_ok=True)

_lock = threading.Lock()
TZ_UTC8 = timezone(timedelta(hours=8))


def _load():
    if NOTIFY_PATH.exists():
        try:
            with open(NOTIFY_PATH) as f:
                return json.load(f)
        except:
            pass
    return []


def _save(items):
    with open(NOTIFY_PATH, "w") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)


def notify(msg: str, level: str = "info"):
    """daemon 调这个写一条通知"""
    with _lock:
        items = _load()
        items.append({
            "time": datetime.now(TZ_UTC8).strftime("%m-%d %H:%M"),
            "level": level,
            "msg": msg,
        })
        _save(items)


def drain() -> list:
    """Hermes watcher 调这个读走所有通知并清空"""
    with _lock:
        items = _load()
        _save([])
        return items


def format_open(position: dict) -> str:
    p = position
    direction = "多" if p["direction"] == "long" else "空"
    return (
        f"📈 开仓 #{p.get('id','?')}\n"
        f"币种: {p['symbol']}\n"
        f"方向: {direction} {p.get('leverage',3)}x\n"
        f"入场: {p['entry_price']}\n"
        f"止损: {p['stop_loss']}\n"
        f"止盈: {p['take_profit']}"
    )


def format_close(position: dict, reason: str, pnl_usd: float) -> str:
    p = position
    direction = "多" if p["direction"] == "long" else "空"
    return (
        f"📉 平仓 #{p.get('id','?')}\n"
        f"币种: {p['symbol']} {direction}\n"
        f"入场: {p['entry_price']}\n"
        f"出场: {p.get('exit_price', '?')}\n"
        f"盈亏: {pnl_usd:+.2f}U\n"
        f"原因: {reason}"
    )


def notify_open(position: dict):
    notify(format_open(position), "trade")


def notify_close(position: dict, reason: str, pnl_usd: float):
    level = "error" if reason == "止损" else "trade"
    notify(format_close(position, reason, pnl_usd), level)


# ===== Hermes File Watcher =====
# daemon 写文件 → Hermes 的 send_message 推微信
# Hermes 在这调 send_message

class HermesFileWatcher:
    """
    监听 notifications.json 变化，推送给用户
    Hermes 进程启动时运行（threading），不阻塞主流程
    """

    def __init__(self, callback_send_message, poll_interval: float = 0.5):
        self.callback = callback_send_message
        self.poll_interval = poll_interval
        self._thread = None
        self._running = False
        self._last_mtime = 0.0
        self._last_size = 0

    def _file_changed(self) -> bool:
        if not NOTIFY_PATH.exists():
            return False
        stat = NOTIFY_PATH.stat()
        if stat.st_mtime > self._last_mtime or stat.st_size != self._last_size:
            self._last_mtime = stat.st_mtime
            self._last_size = stat.st_size
            return True
        return False

    def _loop(self):
        while self._running:
            if self._file_changed():
                try:
                    items = drain()
                    for item in items:
                        try:
                            self.callback(item["msg"])
                        except Exception as e:
                            print(f"[HermesFileWatcher] 发送失败: {e}")
                except Exception as e:
                    print(f"[HermesFileWatcher] 读取失败: {e}")
            time.sleep(self.poll_interval)

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        print(f"[HermesFileWatcher] 启动，监听 {NOTIFY_PATH}")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        print("[HermesFileWatcher] 已停止")


if __name__ == "__main__":
    # 测试
    notify("测试通知", "info")
    items = drain()
    for i in items:
        print(f"[{i['time']}] {i['msg']}")
