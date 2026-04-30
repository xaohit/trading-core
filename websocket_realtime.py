"""
WebSocket Realtime Layer
订阅持仓币的 aggTrade 价格，秒级触发止损止盈
参考 binance-square-monitor market_realtime.py 架构
"""
import json
import threading
import time
import websocket
from datetime import datetime, timezone, timedelta

try:
    from .config import PROXY
    from .db.trades import TradeDB
    from .executor import Executor
    from .memory import Memory
except ImportError:
    from config import PROXY
    from db.trades import TradeDB
    from executor import Executor
    from memory import Memory


TZ_UTC8 = timezone(timedelta(hours=8))
_ws = None
_thread = None
_running = False

# 当前持仓的实时价格 {symbol: price}
_realtime_prices = {}
_last_check = {}


def _now_str():
    return datetime.now(TZ_UTC8).strftime("%m-%d %H:%M")


def _on_message(ws, message):
    """处理每条聚合成交消息"""
    global _realtime_prices
    try:
        data = json.loads(message)
        # Binance WebSocket format: {"e":"aggTrade","s":"BTCUSDT",...}
        if data.get("e") != "aggTrade":
            return
        symbol = data["s"]
        price = float(data["p"])  # 成交价格
        _realtime_prices[symbol] = price
    except:
        pass


def _on_error(ws, error):
    print(f"[WS ERROR] {error}")


def _on_close(ws, close_status_code, close_msg):
    print(f"[WS CLOSE] {close_status_code} {close_msg}")


def _on_open(ws):
    print(f"[WS OPEN]")


def _subscribe_positions():
    """订阅所有持仓币的 aggTrade 流"""
    global _ws
    positions = TradeDB.get_open()
    if not positions:
        return

    streams = [f"{p['symbol'].lower()}@aggTrade" for p in positions]
    subscribe_msg = {
        "method": "SUBSCRIBE",
        "params": streams,
        "id": int(time.time())
    }
    _ws.send(json.dumps(subscribe_msg))
    print(f"[WS SUBSCRIBE] {len(streams)} streams: {[s.split('@')[0] for s in streams]}")


def _monitor_loop():
    """
    主监控循环：每秒检查持仓是否触发止损止盈
    """
    global _running, _realtime_prices
    _running = True
    print("[MONITOR] 实时监控启动")

    while _running:
        try:
            positions = TradeDB.get_open()
            if not positions:
                time.sleep(5)
                _realtime_prices.clear()
                continue

            now = time.time()

            for pos in positions:
                symbol = pos["symbol"]
                price = _realtime_prices.get(symbol)
                if price is None:
                    continue

                direction = pos["direction"]
                entry = pos["entry_price"]
                sl = pos["stop_loss"]
                tp = pos["take_profit"]
                lev = pos["leverage"]
                pos_usd = pos.get("position_usd", 10)

                if direction == "long":
                    pnl_pct = (price - entry) / entry * 100 * lev
                    hit_sl = price <= sl
                    hit_tp = price >= tp
                else:
                    pnl_pct = (entry - price) / entry * 100 * lev
                    hit_sl = price >= sl
                    hit_tp = price <= tp

                if hit_sl or hit_tp:
                    reason = "止损" if hit_sl else "止盈"
                    pnl_usd = round(pnl_pct / 100 * pos_usd, 4)
                    Executor.close_position(
                        pos["id"], price, reason,
                        round(pnl_pct, 2), pnl_usd
                    )
                    Memory.record_outcome(
                        pos["id"], symbol,
                        pos.get("pre_analysis", {}).get("type", ""),
                        direction, pnl_pct, pnl_usd, reason
                    )
                    print(f"[MONITOR] 平仓 {symbol} {pnl_usd:+.2f}U [{reason}]")

        except Exception as e:
            print(f"[MONITOR ERROR] {e}")

        time.sleep(1)


def start():
    """启动 WebSocket 实时层（在新线程中）"""
    global _ws, _thread, _running

    if _running:
        print("[WS] 已经运行中")
        return

    def _run_ws():
        global _ws
        while _running:
            try:
                positions = TradeDB.get_open()
                streams = [f"{p['symbol'].lower()}@aggTrade" for p in positions]
                if not streams:
                    time.sleep(5)
                    continue

                ws_url = "wss://fstream.binance.com/stream"
                _ws = websocket.WebSocketApp(
                    ws_url,
                    on_message=_on_message,
                    on_error=_on_error,
                    on_close=_on_close,
                    on_open=_on_open,
                )
                # 订阅
                subscribe_msg = {
                    "method": "SUBSCRIBE",
                    "params": streams,
                    "id": 1
                }
                _ws.on_message = lambda ws, msg: (_on_message(ws, msg), _resubscribe_if_needed(ws, positions))()

                print(f"[WS] 连接中...")
                # 注意：这个实现是简化版，实际用 websocket-client 库更稳定
                # 下一版本用标准库 + 完整重连逻辑
                _ws.run_forever(ping_interval=30)
            except Exception as e:
                print(f"[WS ERROR] {e}")
                time.sleep(5)

    _thread = threading.Thread(target=_monitor_loop, daemon=True)
    _thread.start()
    print("[WS] 实时层已启动")


def _resubscribe_if_needed(ws, last_positions):
    """检查持仓变化，重新订阅"""
    current = TradeDB.get_open()
    current_symbols = {p["symbol"] for p in current}
    last_symbols = {p["symbol"] for p in last_positions}
    if current_symbols != last_symbols:
        streams = [f"{s.lower()}@aggTrade" for s in current_symbols]
        ws.send(json.dumps({"method": "SUBSCRIBE", "params": streams, "id": 2}))


def stop():
    """停止实时层"""
    global _running, _ws
    _running = False
    if _ws:
        _ws.close()
    print("[WS] 实时层已停止")


def get_realtime_prices() -> dict:
    """获取当前实时价格"""
    return dict(_realtime_prices)
