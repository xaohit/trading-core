#!/usr/bin/env python3
"""
Trading Core Web UI
"""
import json
import time
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn

import sys
sys.path.insert(0, str(Path(__file__).parent))

from db.connection import init_db
from db.trades import TradeDB
from market import Market
from state import State
from paper_balance import PaperBalance

app = FastAPI(title="Trading Core")

TZ_UTC8 = timezone(timedelta(hours=8))

_cache = {}
_cache_lock = threading.Lock()


def _now():
    return datetime.now(TZ_UTC8).strftime("%m-%d %H:%M")


def _cached(key: str, ttl: float, fn):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and (now - hit[0]) < ttl:
            return hit[1]
    value = fn()
    with _cache_lock:
        _cache[key] = (now, value)
    return value


def _invalidate(*keys):
    with _cache_lock:
        if not keys:
            _cache.clear()
        else:
            for k in keys:
                _cache.pop(k, None)


@app.get("/api/dashboard")
def api_dashboard():
    def compute():
        pb = PaperBalance.get()
        balance = pb["balance"]
        equity = pb["equity"]
        open_positions = TradeDB.get_open()
        closed_stats = TradeDB.stats()
        closed_history = TradeDB.get_closed(30)
        state = State()
        scanner_round = state.get("scan_count", 0)
        last_scan = state.get("last_scan", "")
        latest_signals = _format_signals(TradeDB.get_recent_signals(8))

        tickers = Market.all_tickers()
        ticker_map = {t["symbol"]: float(t["lastPrice"]) for t in tickers}

        positions = []
        for pos in open_positions:
            sym = pos["symbol"]
            price = ticker_map.get(sym)
            pnl_pct, pnl_usd = _calc_pnl(pos, price)
            pre = (json.loads(pos["pre_analysis"]) if isinstance(pos.get("pre_analysis"), str) else pos.get("pre_analysis", {}))
            remaining = pos.get("remaining_pct", 100)
            positions.append({
                "id": pos["id"],
                "symbol": sym,
                "direction": pos["direction"],
                "entry_price": pos["entry_price"],
                "current_price": price,
                "pnl_pct": round(pnl_pct, 2),
                "pnl_usd": round(pnl_usd, 4),
                "stop_loss": pos["stop_loss"],
                "take_profit": pos["take_profit"],
                "leverage": pos["leverage"],
                "position_usd": pos.get("position_usd", 10),
                "entry_time": pos["entry_time"],
                "sl_pct": pre.get("sl_pct"),
                "tp_pct": pre.get("tp_pct"),
                "signal_type": pre.get("type", "-"),
                "agent_decision": pre.get("agent_decision", {}),
                "remaining_pct": remaining,
                "tp1_price": pos.get("tp1_price"),
                "tp1_done": pos.get("tp1_done", 0),
                "tp2_price": pos.get("tp2_price"),
                "tp2_done": pos.get("tp2_done", 0),
                "trailing_stop": pos.get("trailing_stop"),
                "atr_pct_at_entry": pre.get("atr_pct"),
            })

        equity_curve = PaperBalance.equity_curve(closed_history)
        # current_equity 是实时值，前端单独高亮，不要拼进历史曲线里造成抖动
        current_equity = round(equity, 4)

        # Social heat leaderboard (Phase 5)
        heat_lb = []
        try:
            from social_heat import get_heat_leaderboard
            heat_lb = get_heat_leaderboard(top_n=10)
        except Exception:
            pass

        decisions = []
        experiences = []
        try:
            from decision_memory import DecisionMemory
            decisions = DecisionMemory.recent_decisions(12)
            experiences = DecisionMemory.recent_experiences(8)
        except Exception:
            pass

        return {
            "updated_at": _now(),
            "initial_capital": pb["initial_capital"],
            "balance": round(balance, 4),
            "equity": round(equity, 4),
            "current_equity": current_equity,
            "closed_pnl": pb["closed_pnl"],
            "unrealized_pnl": pb["unrealized_pnl"],
            "positions": positions,
            "stats": closed_stats,
            "history": [_format_h(h) for h in closed_history],
            "equity_curve": equity_curve,
            "scanner": {
                "round": scanner_round,
                "last_scan": last_scan,
                "latest_signals": latest_signals,
            },
            "heat_leaderboard": heat_lb,
            "evolved_params": _get_evolved_params(),
            "decision_memory": {
                "decisions": decisions,
                "experiences": experiences,
            },
        }
    return _cached("dashboard", 2.0, compute)


@app.get("/api/decisions")
def api_decisions():
    """Returns recent decision history for UI display."""
    try:
        from decision_memory import DecisionMemory
        decisions = DecisionMemory.recent_decisions(12)
        return {"decisions": decisions}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/memory")
def api_memory():
    """Returns recent experience memory entries."""
    try:
        from decision_memory import DecisionMemory
        experiences = DecisionMemory.recent_experiences(8)
        return {"experiences": experiences}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/settings")
def api_settings():
    """Returns a snapshot of current config / evolved params."""
    try:
        from config import (
            MAX_DAILY_TRADES, MAX_DAILY_LOSS_PCT, COOLDOWN_AFTER_LOSS_MINUTES,
            ATR_STOP_MULTIPLIER, RISK_PER_TRADE_PCT, TP1_R_MULTIPLE, TP2_R_MULTIPLE,
            TP1_CLOSE_PCT, TP2_CLOSE_PCT, TRAILING_STOP_ATR_MULT, ATR_LOOKBACK,
            MIN_NOTIONAL_USDT
        )
        # Read evolved params from state.json
        from pathlib import Path
        import json
        state_path = Path.home() / ".hermes" / "trading_core" / "state.json"
        evolved = {}
        if state_path.exists():
            with open(state_path, 'r', encoding='utf-8') as f:
                s = json.load(f)
            evolved = s.get("evolved_params", {})
        return {
            "risk_limits": {
                "MAX_DAILY_TRADES": MAX_DAILY_TRADES,
                "MAX_DAILY_LOSS_PCT": MAX_DAILY_LOSS_PCT,
                "COOLDOWN_AFTER_LOSS_MINUTES": COOLDOWN_AFTER_LOSS_MINUTES,
            },
            "sizing": {
                "ATR_STOP_MULTIPLIER": ATR_STOP_MULTIPLIER,
                "RISK_PER_TRADE_PCT": RISK_PER_TRADE_PCT,
                "TP1_R_MULTIPLE": TP1_R_MULTIPLE,
                "TP2_R_MULTIPLE": TP2_R_MULTIPLE,
                "TP1_CLOSE_PCT": TP1_CLOSE_PCT,
                "TP2_CLOSE_PCT": TP2_CLOSE_PCT,
                "TRAILING_STOP_ATR_MULT": TRAILING_STOP_ATR_MULT,
                "ATR_LOOKBACK": ATR_LOOKBACK,
                "MIN_NOTIONAL_USDT": MIN_NOTIONAL_USDT,
            },
            "evolved_params": evolved,
        }
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/backtest/run")
def api_backtest_run(payload: dict):
    """Trigger a backtest via REST and return results (Phase 7E compatible)."""
    try:
        symbol = payload.get("symbol", "BTCUSDT")
        start = payload.get("start", "2025-01-01")
        end = payload.get("end", "2025-04-01")
        interval = payload.get("interval", "15m")
        from backtest import fetch_klines, BacktestEngine
        klines_by_symbol = {}
        klines = fetch_klines(symbol, interval, start, end)
        if not klines:
            return {"error": "No klines fetched"}
        klines_by_symbol[symbol] = klines
        engine = BacktestEngine(
            symbols=[symbol],
            klines_by_symbol=klines_by_symbol,
            initial_balance=10000.0,
            leverage=3,
            sizing_mode="atr",
            atr_multiplier=1.5,
            risk_pct=2.0,
            cooldown_candles=4,
        )
        result = engine.run()
        return {"result": result}
    except Exception as e:
        return {"error": str(e)}


def _get_evolved_params() -> dict:
    """读取当前演化参数和演化状态"""
    from pathlib import Path
    import json
    state_path = Path.home() / ".hermes" / "trading_core" / "state.json"
    if not state_path.exists():
        return {"evolved": {}, "last_evolution": None, "total_evolved": 0}
    try:
        with open(state_path) as f:
            state = json.load(f)
        evolved = state.get("evolved_params", {})
        last_ts = state.get("last_evolution")
        if last_ts:
            import time
            from datetime import datetime, timezone, timedelta
            TZ_UTC8 = timezone(timedelta(hours=8))
            last_str = datetime.fromtimestamp(last_ts, TZ_UTC8).strftime("%m-%d %H:%M")
        else:
            last_str = None
        return {
            "evolved": evolved,
            "last_evolution": last_str,
            "total_evolved": len(evolved),
        }
    except Exception:
        return {"evolved": {}, "last_evolution": None, "total_evolved": 0}


def _calc_pnl(pos, price):
    if not price:
        return 0, 0
    direction = pos["direction"]
    entry = pos["entry_price"]
    lev = pos["leverage"]
    pos_usd = pos.get("position_usd", 10)
    if direction == "long":
        pnl_pct = (price - entry) / entry * 100 * lev
    else:
        pnl_pct = (entry - price) / entry * 100 * lev
    pnl_usd = pnl_pct / 100 * pos_usd
    return pnl_pct, pnl_usd


def _calc_pnl_usd(pos):
    price = Market.ticker(pos["symbol"])
    if not price:
        return 0
    _, pnl_usd = _calc_pnl(pos, float(price["lastPrice"]))
    return pnl_usd


def _build_equity_curve(balance, history):
    """已废弃，使用 PaperBalance.equity_curve"""
    return [balance]


def _format_h(h):
    return {
        "symbol": h["symbol"],
        "direction": h["direction"],
        "entry_price": h["entry_price"],
        "exit_price": h["exit_price"],
        "pnl_pct": h.get("pnl_pct"),
        "pnl_usd": h.get("pnl_usd"),
        "exit_reason": h["exit_reason"],
        "entry_time": h["entry_time"],
        "exit_time": h.get("exit_time"),
        "strength": (json.loads(h["pre_analysis"]) if isinstance(h.get("pre_analysis"), str) else h.get("pre_analysis", {})).get("type", "-"),
    }


@app.get("/api/signals")
def api_signals():
    """各策略历史统计"""
    from memory import Memory
    stats = Memory.get_strategy_stats()
    recent = _format_signals(Memory.get_recent_signals(limit=20))
    return {"stats": stats, "recent": recent}


@app.get("/api/decision-memory")
def api_decision_memory():
    from decision_memory import DecisionMemory
    return {
        "decisions": DecisionMemory.recent_decisions(30),
        "experiences": DecisionMemory.recent_experiences(30),
    }


@app.get("/api/decision-memory/review-due")
def api_decision_memory_review_due():
    from decision_memory import DecisionMemory
    reviewed = DecisionMemory.review_due(20)
    _invalidate("dashboard")
    return {"ok": True, "reviewed": reviewed}


@app.get("/api/decision-memory/retrieve")
def api_decision_memory_retrieve(
    symbol: str = None,
    signal_type: str = None,
    tags: str = "",
    limit: int = 5,
):
    from decision_memory import DecisionMemory
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    return {
        "experiences": DecisionMemory.retrieve_similar(
            symbol=symbol,
            signal_type=signal_type,
            tags=tag_list,
            limit=max(1, min(limit, 20)),
        )
    }


@app.get("/api/heat-leaderboard")
def api_heat_leaderboard(top_n: int = 20):
    """Social heat leaderboard (Phase 5)."""
    try:
        from social_heat import get_heat_leaderboard
        lb = get_heat_leaderboard(top_n=top_n)
        return {"leaderboard": lb, "ok": True}
    except Exception as exc:
        return {"leaderboard": [], "ok": False, "error": str(exc)}


@app.get("/api/failure-archive")
def api_failure_archive(limit: int = 20):
    """Failure archive with tags (Phase 6)."""
    try:
        from reflection import FailureArchive
        failures = FailureArchive.get_recent_failures(limit=limit)
        tag_stats = FailureArchive.get_tag_stats()
        return {"failures": failures, "tag_stats": tag_stats, "ok": True}
    except Exception as exc:
        return {"failures": [], "tag_stats": [], "ok": False, "error": str(exc)}


@app.get("/api/strategy-weights")
def api_strategy_weights():
    """Adaptive strategy weights (Phase 6)."""
    try:
        from reflection import StrategyWeighter, RuleReflector
        weights = StrategyWeighter.get_strategy_priority()
        suggestions = RuleReflector.get_suggestions()
        return {"weights": weights, "suggestions": suggestions, "ok": True}
    except Exception as exc:
        return {"weights": [], "suggestions": [], "ok": False, "error": str(exc)}


def _json_field(row: dict, key: str, default):
    raw = row.get(key)
    if raw in (None, ""):
        return default
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return default


def _format_signals(rows: list[dict]) -> list[dict]:
    formatted = []
    for row in rows:
        analysis = _json_field(row, "analysis_json", {})
        snapshot = _json_field(row, "snapshot_json", {})
        tags = _json_field(row, "tags", analysis.get("tags", []))
        notes = _json_field(row, "notes", analysis.get("notes", []))
        formatted.append({
            "id": row.get("id"),
            "scanned_at": row.get("scanned_at"),
            "symbol": row.get("symbol"),
            "signal_type": row.get("signal_type"),
            "strength": row.get("strength"),
            "direction": row.get("direction"),
            "price": row.get("price"),
            "funding_rate": row.get("funding_rate"),
            "change_24h": row.get("change_24h"),
            "score": row.get("score"),
            "verdict": row.get("verdict") or analysis.get("verdict"),
            "tags": tags,
            "notes": notes,
            "action": row.get("action"),
            "result": row.get("result"),
            "snapshot": snapshot,
            "analysis": analysis,
        })
    return formatted


@app.get("/api/close/{trade_id}")
def api_close(trade_id: int):
    from executor import Executor
    positions = TradeDB.get_open()
    pos = next((p for p in positions if p["id"] == trade_id), None)
    if not pos:
        return {"ok": False, "error": "持仓不存在"}
    Executor._close_by_market(pos)
    _invalidate("dashboard")
    return {"ok": True}


@app.get("/api/close-all")
def api_close_all():
    from executor import Executor
    count = Executor.close_all()
    _invalidate("dashboard")
    return {"ok": True, "count": count}


HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<title>Trading Core</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root {
  --bg: #0a0e17;
  --surface: #111827;
  --card: #1a2035;
  --border: #1f2d45;
  --text: #e2e8f0;
  --muted: #64748b;
  --accent: #f0b90b;
  --green: #00c853;
  --red: #ff3b5c;
  --blue: #3b82f6;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: var(--bg);
  color: var(--text);
  font-size: 13px;
  min-height: 100vh;
}

/* ---- Header ---- */
.header {
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  padding: 14px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: sticky;
  top: 0;
  z-index: 100;
}
.header-left { display: flex; align-items: center; gap: 16px; }
.logo {
  font-size: 18px;
  font-weight: 700;
  color: var(--accent);
  letter-spacing: -0.5px;
}
.status-badge {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--muted);
}
.dot {
  width: 8px; height: 8px;
  border-radius: 50%;
  background: var(--muted);
}
.dot.running {
  background: var(--green);
  box-shadow: 0 0 6px var(--green);
  animation: pulse 2s infinite;
}
@keyframes pulse {
  0%,100% { opacity: 1; }
  50% { opacity: 0.4; }
}
.header-right {
  display: flex;
  align-items: flex-end;
  gap: 24px;
}
.metric-label {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--muted);
  margin-bottom: 2px;
}
.metric-value {
  font-size: 22px;
  font-weight: 700;
  line-height: 1;
}
.metric-value.positive { color: var(--green); }
.metric-value.negative { color: var(--red); }
.metric-sub {
  font-size: 11px;
  color: var(--muted);
  margin-top: 2px;
}

/* ---- Main Grid ---- */
.main {
  display: grid;
  grid-template-columns: 1fr 340px;
  gap: 16px;
  padding: 16px 24px;
  max-width: 1400px;
}

/* ---- Cards ---- */
.card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
}
.card-header {
  padding: 14px 16px 10px;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.card-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
  display: flex;
  align-items: center;
  gap: 6px;
}
.card-body { padding: 12px 16px; }

/* ---- Stats Bar ---- */
.stats-bar {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  padding: 14px 16px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  margin-bottom: 16px;
}
.stat-item { text-align: center; }
.stat-label {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--muted);
  margin-bottom: 4px;
}
.stat-val {
  font-size: 18px;
  font-weight: 700;
}
.stat-val.green { color: var(--green); }
.stat-val.red { color: var(--red); }
.stat-val.accent { color: var(--accent); }
.stat-val.blue { color: var(--blue); }

/* ---- Positions ---- */
.pos-table { width: 100%; border-collapse: collapse; }
.pos-table th {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--muted);
  text-align: left;
  padding: 6px 10px;
  border-bottom: 1px solid var(--border);
  font-weight: 500;
}
.pos-table td {
  padding: 10px 10px;
  border-bottom: 1px solid #1a2438;
  vertical-align: middle;
}
.pos-table tr:last-child td { border-bottom: none; }
.pos-table tr:hover td { background: #1a2438; }

.sym { font-weight: 700; font-size: 13px; }
.dir-long { color: var(--green); font-weight: 700; font-size: 12px; }
.dir-short { color: var(--red); font-weight: 700; font-size: 12px; }
.pnl-pos { color: var(--green); font-weight: 600; }
.pnl-neg { color: var(--red); font-weight: 600; }
.price { font-size: 12px; color: var(--muted); }
.tag {
  display: inline-block;
  padding: 2px 7px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 600;
}
.tag-sl { background: #2d1a1a; color: var(--red); }
.tag-tp { background: #1a2d1a; color: var(--green); }
.tag-s { background: #1a1a3d; color: #a0b4ff; }
.tag-a { background: #2d2d1a; color: #ffe066; }
.tag-b { background: #1a2d2d; color: #66e0ff; }

.btn {
  border: none;
  border-radius: 6px;
  padding: 5px 12px;
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  transition: opacity 0.15s;
}
.btn:hover { opacity: 0.8; }
.btn-close {
  background: #2d1a1a;
  color: var(--red);
  border: 1px solid #3d2020;
}
.btn-close:hover { background: #3d2020; }
.btn-all {
  background: #3d1515;
  color: var(--red);
  border: 1px solid #5d2020;
}
.btn-all:hover { background: #4d2020; }

/* ---- History ---- */
.history-table { width: 100%; border-collapse: collapse; }
.history-table th {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--muted);
  text-align: left;
  padding: 6px 10px;
  border-bottom: 1px solid var(--border);
  font-weight: 500;
}
.history-table td {
  padding: 8px 10px;
  border-bottom: 1px solid #1a2438;
  font-size: 12px;
}
.history-table tr:last-child td { border-bottom: none; }
.reason-tag {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 10px;
}
.reason-tp { background: #1a3d1a; color: var(--green); }
.reason-sl { background: #3d1a1a; color: var(--red); }
.reason-mg { background: #1a1a3d; color: #a0b4ff; }

/* ---- Chart ---- */
.chart-wrap {
  padding: 12px 16px;
  height: 200px;
}
canvas { width: 100% !important; height: 100% !important; }

/* ---- Sidebar ---- */
.sidebar { display: flex; flex-direction: column; gap: 16px; }

/* ---- Scan Info ---- */
.scan-info { font-size: 12px; }
.scan-row {
  display: flex;
  justify-content: space-between;
  padding: 6px 0;
  border-bottom: 1px solid #1a2438;
  font-size: 12px;
}
.scan-row:last-child { border-bottom: none; }
.scan-key { color: var(--muted); }
.scan-val { color: var(--text); font-weight: 500; }

/* ---- Scanner control ---- */
.scanner-card { }
.scanner-btn {
  width: 100%;
  background: var(--accent);
  color: #000;
  border: none;
  border-radius: 8px;
  padding: 10px;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
  margin-top: 10px;
}
.scanner-btn:hover { opacity: 0.85; }

/* ---- Refresh ---- */
.refresh-bar {
  text-align: center;
  padding: 6px;
  font-size: 11px;
  color: var(--muted);
  background: var(--surface);
  border-top: 1px solid var(--border);
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
}

/* ---- Empty ---- */
.empty-state {
  padding: 32px;
  text-align: center;
  color: var(--muted);
  font-size: 13px;
}
</style>
</head>
<body>

<!-- Header -->
<div class="header">
  <div class="header-left">
    <div class="logo">Trading Core</div>
    <div class="status-badge">
      <span class="dot" id="scanDot"></span>
      <span id="scanStatus">扫描器待机</span>
    </div>
  </div>
  <div class="header-right">
    <div>
      <div class="metric-label">总权益</div>
      <div class="metric-value" id="equity">-</div>
    </div>
    <div>
      <div class="metric-label">余额</div>
      <div class="metric-value" style="color:var(--accent);" id="balance">-</div>
    </div>
    <div>
      <div class="metric-label">浮盈亏</div>
      <div class="metric-value" id="unrealPnl">-</div>
    </div>
  </div>
</div>

<!-- Stats Bar -->
<div style="padding: 12px 24px; max-width: 1400px;">
  <div class="stats-bar">
    <div class="stat-item">
      <div class="stat-label">交易次数</div>
      <div class="stat-val" id="s-total">-</div>
    </div>
    <div class="stat-item">
      <div class="stat-label">胜率</div>
      <div class="stat-val green" id="s-winrate">-</div>
    </div>
    <div class="stat-item">
      <div class="stat-label">总盈亏</div>
      <div class="stat-val accent" id="s-pnl">-</div>
    </div>
    <div class="stat-item">
      <div class="stat-label">持仓数</div>
      <div class="stat-val blue" id="s-pos">-</div>
    </div>
  </div>
</div>

<!-- Main Grid -->
<div class="main">

  <!-- Left Column -->
  <div class="left-col">
    <!-- Positions -->
    <div class="card">
      <div class="card-header">
        <div class="card-title">📊 持仓 <span id="posCount" style="color:var(--muted);font-weight:400;font-size:12px;">(0)</span></div>
        <button class="btn btn-all" onclick="closeAll()">全部平仓</button>
      </div>
      <div id="positionsPanel">
        <div class="empty-state">暂无持仓</div>
      </div>
    </div>

    <!-- History -->
    <div class="card" style="margin-top:16px;">
      <div class="card-header">
        <div class="card-title">📋 最近交易</div>
      </div>
      <div id="historyPanel">
        <div class="empty-state">暂无历史记录</div>
      </div>
    </div>
  </div>

  <!-- Right Column -->
  <div class="sidebar">

    <!-- Equity Chart -->
    <div class="card">
      <div class="card-header">
        <div class="card-title">📈 权益曲线</div>
      </div>
      <div class="chart-wrap">
        <canvas id="equityChart"></canvas>
      </div>
    </div>

    <!-- Scanner Info -->
    <div class="card scanner-card">
      <div class="card-header">
        <div class="card-title">🔍 扫描器</div>
      </div>
      <div class="card-body">
        <div class="scan-info">
          <div class="scan-row">
            <span class="scan-key">扫描轮次</span>
            <span class="scan-val" id="scanRound">-</span>
          </div>
          <div class="scan-row">
            <span class="scan-key">上次扫描</span>
            <span class="scan-val" id="scanLast">-</span>
          </div>
          <div class="scan-row">
            <span class="scan-key">策略</span>
            <span class="scan-val">4策略混合</span>
          </div>
          <div class="scan-row">
            <span class="scan-key">扫描间隔</span>
            <span class="scan-val">5分钟</span>
          </div>
        </div>
        <button class="scanner-btn" onclick="triggerScan()">▶ 手动扫描</button>
      </div>
    </div>

  </div>
</div>

<div class="refresh-bar" id="updated">加载中...</div>

<script>
const fmtPct = (v) => {
  if (v == null || isNaN(v)) return '<span class="pnl-neg">-</span>';
  const cls = v > 0 ? 'pnl-pos' : v < 0 ? 'pnl-neg' : '';
  const sign = v > 0 ? '+' : '';
  return `<span class="${cls}">${sign}${v.toFixed(2)}%</span>`;
};
const fmtPnl = (v) => {
  if (v == null || isNaN(v)) return '-';
  const cls = v > 0 ? 'pnl-pos' : v < 0 ? 'pnl-neg' : '';
  const sign = v > 0 ? '+' : '';
  return `<span class="${cls}">${sign}${v.toFixed(4)} U</span>`;
};
const fmtPrice = (v) => {
  if (!v) return '-';
  return parseFloat(v).toPrecision(5);
};
const strengthTag = (s) => {
  const map = { 'S': ['tag-s', 'S级'], 'A': ['tag-a', 'A级'], 'B': ['tag-b', 'B级'] };
  const [cls, label] = map[s] || ['tag-a', s || '-'];
  return `<span class="tag ${cls}">${label}</span>`;
};
const reasonTag = (r) => {
  if (r === '止盈') return `<span class="reason-tag reason-tp">TP</span>`;
  if (r === '止损') return `<span class="reason-tag reason-sl">SL</span>`;
  return `<span class="reason-tag reason-mg">${r || '-'}</span>`;
};

let equityChart = null;
let lastData = null;

function renderEquityCurve(curve) {
  const ctx = document.getElementById('equityChart').getContext('2d');
  const labels = curve.map((_, i) => i);

  if (equityChart) equityChart.destroy();

  const first = curve[0] || 0;
  const last = curve[curve.length - 1] || 0;
  const color = last >= first ? '#00c853' : '#ff3b5c';

  equityChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        data: curve,
        borderColor: color,
        backgroundColor: color + '22',
        fill: true,
        tension: 0.4,
        borderWidth: 2,
        pointRadius: 0,
        pointHoverRadius: 4,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: {
        callbacks: { label: (ctx) => ctx.parsed.y.toFixed(4) + ' U' }
      }},
      scales: {
        x: { display: false },
        y: {
          grid: { color: '#1a2438' },
          ticks: { color: '#64748b', font: { size: 10 }, callback: (v) => v.toFixed(0) }
        }
      }
    }
  });
}

function render(data) {
  lastData = data;

  // Header metrics
  document.getElementById('equity').textContent = data.equity.toFixed(2) + ' U';
  document.getElementById('balance').textContent = data.balance.toFixed(2) + ' U';

  const unrealPnl = data.positions.reduce((sum, p) => sum + (p.pnl_usd || 0), 0);
  const unrealPct = document.getElementById('unrealPnl');
  unrealPnlEl = unrealPnl;
  unrealPnlEl.className = 'metric-value ' + (unrealPnl >= 0 ? 'positive' : 'negative');
  unrealPnlEl.textContent = (unrealPnl >= 0 ? '+' : '') + unrealPnl.toFixed(4) + ' U';

  // Stats bar
  const s = data.stats;
  document.getElementById('s-total').textContent = s.total || 0;
  document.getElementById('s-winrate').textContent = s.total > 0 ? s.win_rate.toFixed(1) + '%' : '-';
  document.getElementById('s-pnl').textContent = (s.pnl_usd >= 0 ? '+' : '') + (s.pnl_usd || 0).toFixed(4) + ' U';
  document.getElementById('s-pos').textContent = data.positions.length;

  // Scanner status
  const dot = document.getElementById('scanDot');
  const sinfo = document.getElementById('scanStatus');
  const scanRound = document.getElementById('scanRound');
  const scanLast = document.getElementById('scanLast');
  if (data.scanner.last_scan) {
    dot.className = 'dot running';
    sinfo.textContent = '运行中';
    scanRound.textContent = '第 ' + data.scanner.round + ' 轮';
    scanLast.textContent = data.scanner.last_scan;
  } else {
    dot.className = 'dot';
    sinfo.textContent = '待机';
    scanRound.textContent = '-';
    scanLast.textContent = '-';
  }

  // Positions
  const posPanel = document.getElementById('positionsPanel');
  document.getElementById('posCount').textContent = `(${data.positions.length})`;
  if (!data.positions.length) {
    posPanel.innerHTML = '<div class="empty-state">暂无持仓</div>';
  } else {
    posPanel.innerHTML = `<table class="pos-table">
      <thead><tr>
        <th>币种</th><th>方向</th><th>入场价</th><th>当前价</th>
        <th>止损</th><th>TP阶段</th><th>剩余</th><th class="pnl-neg">浮盈亏</th><th></th>
      </tr></thead>
      <tbody>${data.positions.map(p => `
        <tr>
          <td>
            <div class="sym">${p.symbol}</div>
            <div style="font-size:10px;color:var(--muted);margin-top:2px;">${p.signal_type !== '-' ? strengthTag(p.signal_type.replace('neg_funding_long','NFL').replace('pos_funding_short','PFS').replace('crash_bounce_long','CBL').replace('pump_short','PMS')) : ''}</div>
          </td>
          <td class="${p.direction === 'long' ? 'dir-long' : 'dir-short'}">${p.direction === 'long' ? '多' : '空'}</td>
          <td>${fmtPrice(p.entry_price)}</td>
          <td style="color:${p.current_price > p.entry_price && p.direction==='long' || p.current_price < p.entry_price && p.direction==='short' ? 'var(--green)' : p.current_price < p.entry_price && p.direction==='long' || p.current_price > p.entry_price && p.direction==='short' ? 'var(--red)' : 'var(--muted)'}">${fmtPrice(p.current_price)}</td>
          <td>
            <span class="tag tag-sl">${fmtPrice(p.stop_loss)}</span>
          </td>
          <td>
            ${renderTpStages(p)}
          </td>
          <td style="font-weight:600;color:${(p.remaining_pct||100) < 100 ? 'var(--accent)' : 'var(--text)'}">${p.remaining_pct || 100}%</td>
          <td>
            <div>${fmtPct(p.pnl_pct)}</div>
            <div style="font-size:10px;color:var(--muted)">${fmtPnl(p.pnl_usd)}</div>
          </td>
          <td><button class="btn btn-close" onclick="closePos(${p.id})">平仓</button></td>
        </tr>`).join('')}</tbody>
    </table>`;
  }

  function renderTpStages(p) {
    const parts = [];
    if (p.tp1_price) {
      const cls = p.tp1_done ? 'tag-tp' : 'tag-b';
      parts.push(`<span class="tag ${cls}">TP1 ${fmtPrice(p.tp1_price)}</span>`);
    }
    if (p.tp2_price) {
      const cls = p.tp2_done ? 'tag-tp' : 'tag-a';
      parts.push(`<span class="tag ${cls}">TP2 ${fmtPrice(p.tp2_price)}</span>`);
    }
    if (p.tp1_done && p.trailing_stop) {
      parts.push(`<span class="tag tag-sl">追踪 ${fmtPrice(p.trailing_stop)}</span>`);
    }
    return parts.join(' ');
  }

  // History
  const histPanel = document.getElementById('historyPanel');
  if (!data.history.length) {
    histPanel.innerHTML = '<div class="empty-state">暂无历史记录</div>';
  } else {
    histPanel.innerHTML = `<table class="history-table">
      <thead><tr>
        <th>时间</th><th>币种</th><th>方向</th><th>入场</th><th>出场</th><th>盈亏</th><th>原因</th>
      </tr></thead>
      <tbody>${data.history.map(h => `
        <tr>
          <td style="color:var(--muted)">${h.entry_time || '-'}</td>
          <td><strong>${h.symbol}</strong></td>
          <td class="${h.direction === 'long' ? 'dir-long' : 'dir-short'}">${h.direction === 'long' ? '多' : '空'}</td>
          <td>${fmtPrice(h.entry_price)}</td>
          <td>${fmtPrice(h.exit_price)}</td>
          <td>${fmtPct(h.pnl_pct)} ${fmtPnl(h.pnl_usd)}</td>
          <td>${reasonTag(h.exit_reason)}</td>
        </tr>`).join('')}</tbody>
    </table>`;
  }

  // Equity curve
  if (data.equity_curve && data.equity_curve.length > 1) {
    renderEquityCurve(data.equity_curve);
  }

  document.getElementById('updated').textContent = '更新于 ' + data.updated_at + ' · 3秒自动刷新';
}

let unrealPnlEl = null;

async function load() {
  try {
    const resp = await fetch('/api/dashboard');
    const d = await resp.json();
    render(d);
  } catch(e) {
    console.error(e);
  }
}

async function closePos(id) {
  if (!confirm('确认平仓 #' + id + '？')) return;
  await fetch('/api/close/' + id);
  load();
}

async function closeAll() {
  if (!confirm('确认全部平仓？')) return;
  await fetch('/api/close-all');
  load();
}

async function triggerScan() {
  const btn = document.querySelector('.scanner-btn');
  btn.textContent = '⏳ 扫描中...';
  btn.disabled = true;
  try {
    await fetch('/api/scan');
    load();
  } finally {
    btn.textContent = '▶ 手动扫描';
    btn.disabled = false;
  }
}

load();
setInterval(load, 3000);
</script>
</body>
</html>
"""


AGENT_WORKBENCH_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Trading Core Agent Workbench</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root {
  --bg: #101114;
  --panel: #181a20;
  --panel-2: #20232b;
  --line: #2b303a;
  --text: #eef1f5;
  --muted: #9aa3b2;
  --soft: #c7ced9;
  --yellow: #f2b705;
  --green: #21c77a;
  --red: #ef476f;
  --blue: #4ea1ff;
  --cyan: #30c7d8;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-size: 13px;
}
button { font: inherit; }
.topbar {
  height: 64px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 22px;
  background: #15171c;
  border-bottom: 1px solid var(--line);
  position: sticky;
  top: 0;
  z-index: 10;
}
.brand { display: flex; flex-direction: column; gap: 3px; }
.brand strong { font-size: 18px; letter-spacing: 0; }
.brand span { color: var(--muted); font-size: 12px; }
.top-actions { display: flex; align-items: center; gap: 10px; }
.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--soft);
  background: var(--panel-2);
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 8px 10px;
}
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); }
.dot.on { background: var(--green); box-shadow: 0 0 8px rgba(33,199,122,.7); }
.btn {
  border: 1px solid var(--line);
  background: var(--panel-2);
  color: var(--text);
  border-radius: 6px;
  padding: 8px 11px;
  cursor: pointer;
}
.btn.primary { background: var(--yellow); border-color: var(--yellow); color: #171717; font-weight: 700; }
.btn.danger { color: var(--red); border-color: rgba(239,71,111,.45); }
.layout {
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr) 360px;
  gap: 14px;
  padding: 14px;
  max-width: 1680px;
  margin: 0 auto 36px;
}
.panel {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  overflow: hidden;
}
.panel + .panel { margin-top: 14px; }
.panel-head {
  min-height: 44px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--line);
}
.panel-title { font-weight: 700; }
.panel-sub { color: var(--muted); font-size: 12px; }
.panel-body { padding: 14px; }
.metrics { display: grid; gap: 10px; }
.metric {
  background: var(--panel-2);
  border: 1px solid var(--line);
  border-radius: 7px;
  padding: 12px;
}
.metric label {
  display: block;
  color: var(--muted);
  font-size: 11px;
  margin-bottom: 6px;
}
.metric strong { font-size: 22px; line-height: 1; }
.metric small { display: block; margin-top: 5px; color: var(--muted); }
.green { color: var(--green); }
.red { color: var(--red); }
.yellow { color: var(--yellow); }
.blue { color: var(--blue); }
.pipeline {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 8px;
}
.stage {
  background: var(--panel-2);
  border: 1px solid var(--line);
  border-radius: 7px;
  min-height: 82px;
  padding: 10px;
}
.stage b { display: block; font-size: 12px; margin-bottom: 8px; }
.stage span { color: var(--muted); font-size: 11px; line-height: 1.45; }
.stage.active { border-color: rgba(242,183,5,.75); }
.stage.good { border-color: rgba(33,199,122,.45); }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th {
  color: var(--muted);
  font-size: 11px;
  text-align: left;
  font-weight: 600;
  padding: 8px 10px;
  border-bottom: 1px solid var(--line);
}
td {
  padding: 10px;
  border-bottom: 1px solid #252a33;
  vertical-align: top;
}
tr:last-child td { border-bottom: none; }
.symbol { font-weight: 800; }
.muted { color: var(--muted); }
.chip {
  display: inline-flex;
  align-items: center;
  border: 1px solid var(--line);
  background: var(--panel-2);
  border-radius: 5px;
  padding: 3px 7px;
  font-size: 11px;
  color: var(--soft);
  margin: 0 4px 4px 0;
}
.chip.green { border-color: rgba(33,199,122,.4); color: var(--green); }
.chip.red { border-color: rgba(239,71,111,.4); color: var(--red); }
.chip.yellow { border-color: rgba(242,183,5,.45); color: var(--yellow); }
.timeline { display: grid; gap: 8px; }
.event {
  border: 1px solid var(--line);
  background: var(--panel-2);
  border-radius: 7px;
  padding: 10px;
}
.event-top { display: flex; justify-content: space-between; gap: 10px; margin-bottom: 6px; }
.event-reason { color: var(--soft); line-height: 1.45; overflow-wrap: anywhere; }
.heat-list { display: grid; gap: 8px; }
.heat-row {
  display: grid;
  grid-template-columns: 72px 1fr auto;
  gap: 8px;
  align-items: center;
}
.bar { height: 7px; background: #2b303a; border-radius: 999px; overflow: hidden; }
.bar i { display: block; height: 100%; background: var(--yellow); }
.empty {
  color: var(--muted);
  padding: 24px;
  text-align: center;
}
.chart-box { height: 180px; }
.footer {
  position: fixed;
  left: 0; right: 0; bottom: 0;
  padding: 7px 14px;
  color: var(--muted);
  background: #15171c;
  border-top: 1px solid var(--line);
  font-size: 12px;
}
@media (max-width: 1180px) {
  .layout { grid-template-columns: 1fr; }
  .pipeline { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .topbar { height: auto; align-items: flex-start; gap: 12px; flex-direction: column; padding: 14px; }
}
</style>
</head>
<body>
<header class="topbar">
  <div class="brand">
    <strong>Trading Core Agent Workbench</strong>
    <span>纸交易优先 · 决策留痕 · Agent 学习闭环</span>
  </div>
  <div class="top-actions">
    <span class="status-pill"><i id="scanDot" class="dot"></i><span id="scanStatus">待机</span></span>
    <button class="btn primary" id="scanBtn" onclick="triggerScan()">手动扫描</button>
    <button class="btn danger" onclick="closeAll()">全部平仓</button>
  </div>
</header>

<main class="layout">
  <aside>
    <section class="panel">
      <div class="panel-head"><div><div class="panel-title">账户</div><div class="panel-sub" id="updatedAt">加载中</div></div></div>
      <div class="panel-body metrics">
        <div class="metric"><label>权益</label><strong id="equity">-</strong><small id="closedPnl">已实现 -</small></div>
        <div class="metric"><label>余额</label><strong class="yellow" id="balance">-</strong><small>纸交易账户</small></div>
        <div class="metric"><label>浮动盈亏</label><strong id="unrealPnl">-</strong><small id="openCount">持仓 -</small></div>
      </div>
    </section>
    <section class="panel">
      <div class="panel-head"><div class="panel-title">运行状态</div></div>
      <div class="panel-body metrics">
        <div class="metric"><label>扫描轮次</label><strong id="scanRound">-</strong><small id="scanLast">上次扫描 -</small></div>
        <div class="metric"><label>胜率</label><strong class="green" id="winRate">-</strong><small id="tradeStats">交易 -</small></div>
      </div>
    </section>
  </aside>

  <section>
    <section class="panel">
      <div class="panel-head">
        <div><div class="panel-title">决策流水线</div><div class="panel-sub">规则找机会，Agent 做判断，硬风控兜底</div></div>
      </div>
      <div class="panel-body">
        <div class="pipeline">
          <div class="stage good"><b>Signal</b><span>资金费率、暴涨暴跌、社交热度发现候选</span></div>
          <div class="stage good"><b>Context</b><span>OI、LSR、taker、盘口、ATR 形成市场标签</span></div>
          <div class="stage active"><b>Pipeline</b><span>环境、分数、入场质量、账户风险</span></div>
          <div class="stage active"><b>Agent Gate</b><span>结合经验库输出 conviction 与 trade/wait</span></div>
          <div class="stage"><b>TA/RR</b><span>检查技术结构，要求 R/R >= 1.5</span></div>
          <div class="stage"><b>Memory</b><span>记录开仓和拒绝，24h 后复盘</span></div>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head"><div><div class="panel-title">持仓</div><div class="panel-sub" id="positionSubtitle">暂无持仓</div></div></div>
      <div class="table-wrap" id="positionsPanel"><div class="empty">暂无持仓</div></div>
    </section>

    <section class="panel">
      <div class="panel-head"><div><div class="panel-title">最近信号与决策</div><div class="panel-sub">展示每个信号最终被哪一层处理</div></div></div>
      <div class="table-wrap" id="signalsPanel"><div class="empty">暂无信号</div></div>
    </section>

    <section class="panel">
      <div class="panel-head"><div class="panel-title">权益曲线</div></div>
      <div class="panel-body"><div class="chart-box"><canvas id="equityChart"></canvas></div></div>
    </section>
  </section>

  <aside>
    <section class="panel">
      <div class="panel-head"><div><div class="panel-title">Agent 记忆</div><div class="panel-sub">最近决策快照</div></div><button class="btn" onclick="reviewDue()">复盘到期</button></div>
      <div class="panel-body timeline" id="decisionPanel"><div class="empty">暂无决策</div></div>
    </section>
    <section class="panel">
      <div class="panel-head"><div><div class="panel-title">经验库</div><div class="panel-sub">最近沉淀的经验</div></div></div>
      <div class="panel-body timeline" id="experiencePanel"><div class="empty">暂无经验</div></div>
    </section>
    <section class="panel">
      <div class="panel-head"><div><div class="panel-title">社交热度</div><div class="panel-sub">候选币线索</div></div></div>
      <div class="panel-body heat-list" id="heatPanel"><div class="empty">暂无热度</div></div>
    </section>
  </aside>
</main>

<div class="footer" id="footer">加载中</div>

<script>
const $ = (id) => document.getElementById(id);
const num = (v, d = 0) => Number.isFinite(Number(v)) ? Number(v) : d;
const price = (v) => v ? Number(v).toPrecision(6) : '-';
const money = (v) => `${num(v).toFixed(2)} U`;
const signedMoney = (v) => `${num(v) >= 0 ? '+' : ''}${num(v).toFixed(4)} U`;
const pct = (v) => `${num(v) >= 0 ? '+' : ''}${num(v).toFixed(2)}%`;
const clsPnL = (v) => num(v) >= 0 ? 'green' : 'red';
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
let equityChart = null;

function chip(text, cls = '') {
  return `<span class="chip ${cls}">${esc(text)}</span>`;
}

function actionClass(action) {
  if (action === 'opened' || action === 'target_hit' || action === 'direction_correct') return 'green';
  if ((action || '').includes('reject') || action === 'invalidated' || action === 'direction_wrong') return 'red';
  return 'yellow';
}

function render(data) {
  $('equity').textContent = money(data.equity);
  $('balance').textContent = money(data.balance);
  $('closedPnl').textContent = `已实现 ${signedMoney(data.closed_pnl || 0)}`;
  const unreal = (data.positions || []).reduce((s, p) => s + num(p.pnl_usd), 0);
  $('unrealPnl').textContent = signedMoney(unreal);
  $('unrealPnl').className = clsPnL(unreal);
  $('openCount').textContent = `持仓 ${data.positions.length}`;
  $('scanRound').textContent = data.scanner?.round || 0;
  $('scanLast').textContent = `上次扫描 ${data.scanner?.last_scan || '-'}`;
  $('winRate').textContent = data.stats?.total ? `${num(data.stats.win_rate).toFixed(1)}%` : '-';
  $('tradeStats').textContent = `交易 ${data.stats?.total || 0} · PnL ${signedMoney(data.stats?.pnl_usd || 0)}`;
  $('updatedAt').textContent = data.updated_at || '-';
  $('footer').textContent = `更新 ${data.updated_at || '-'} · 每 3 秒刷新 · 当前为纸交易工作台`;

  const running = Boolean(data.scanner?.last_scan);
  $('scanDot').className = running ? 'dot on' : 'dot';
  $('scanStatus').textContent = running ? '运行中' : '待机';

  renderPositions(data.positions || []);
  renderSignals(data.scanner?.latest_signals || []);
  renderMemory(data.decision_memory || {});
  renderHeat(data.heat_leaderboard || []);
  renderChart(data.equity_curve || []);
}

function renderPositions(positions) {
  $('positionSubtitle').textContent = positions.length ? `${positions.length} 个开放仓位` : '暂无持仓';
  if (!positions.length) {
    $('positionsPanel').innerHTML = '<div class="empty">暂无持仓</div>';
    return;
  }
  $('positionsPanel').innerHTML = `<table><thead><tr>
    <th>币种</th><th>方向</th><th>价格</th><th>风控</th><th>Agent</th><th>浮盈亏</th><th></th>
  </tr></thead><tbody>${positions.map(p => {
    const ad = p.agent_decision || {};
    return `<tr>
      <td><div class="symbol">${esc(p.symbol)}</div><div class="muted">${esc(p.signal_type || '-')}</div></td>
      <td>${chip(p.direction === 'long' ? '做多' : '做空', p.direction === 'long' ? 'green' : 'red')}</td>
      <td><div>入场 ${price(p.entry_price)}</div><div class="muted">当前 ${price(p.current_price)}</div></td>
      <td><div>${chip('SL ' + price(p.stop_loss), 'red')}</div><div>${chip('TP1 ' + price(p.tp1_price), p.tp1_done ? 'green' : '')}${chip('TP2 ' + price(p.tp2_price), p.tp2_done ? 'green' : '')}</div></td>
      <td><div>${chip('conv ' + (ad.conviction ?? '-'), ad.approved ? 'green' : 'yellow')}</div><div class="muted">${esc(ad.reasoning || '-')}</div></td>
      <td><strong class="${clsPnL(p.pnl_usd)}">${signedMoney(p.pnl_usd)}</strong><div class="${clsPnL(p.pnl_pct)}">${pct(p.pnl_pct)}</div></td>
      <td><button class="btn danger" onclick="closePos(${p.id})">平仓</button></td>
    </tr>`;
  }).join('')}</tbody></table>`;
}

function renderSignals(signals) {
  if (!signals.length) {
    $('signalsPanel').innerHTML = '<div class="empty">暂无信号</div>';
    return;
  }
  $('signalsPanel').innerHTML = `<table><thead><tr>
    <th>时间</th><th>币种</th><th>信号</th><th>分数</th><th>结果</th><th>标签</th>
  </tr></thead><tbody>${signals.map(s => `<tr>
    <td class="muted">${esc(s.scanned_at || '-')}</td>
    <td><div class="symbol">${esc(s.symbol)}</div><div class="muted">${esc(s.direction || '-')}</div></td>
    <td>${chip(s.signal_type || '-', s.strength === 'S' ? 'green' : s.strength === 'B' ? 'yellow' : '')}</td>
    <td><strong>${s.score ?? '-'}</strong><div class="muted">${esc(s.verdict || '-')}</div></td>
    <td>${chip(s.action || '-', actionClass(s.action))}<div class="muted">${esc(s.result || '')}</div></td>
    <td>${(s.tags || []).slice(0, 5).map(t => chip(t)).join('')}</td>
  </tr>`).join('')}</tbody></table>`;
}

function renderMemory(memory) {
  const decisions = memory.decisions || [];
  const experiences = memory.experiences || [];
  $('decisionPanel').innerHTML = decisions.length ? decisions.slice(0, 8).map(d => `
    <div class="event">
      <div class="event-top"><strong>${esc(d.symbol)} · ${esc(d.action)}</strong>${chip(d.status || '-', actionClass(d.status))}</div>
      <div>${chip(d.signal_type || '-')}${chip('conv ' + (d.conviction ?? '-'))}${chip(d.direction || '-')}</div>
      <div class="event-reason">${esc(d.reasoning || d.agent_reasoning || '-')}</div>
    </div>`).join('') : '<div class="empty">暂无决策</div>';
  $('experiencePanel').innerHTML = experiences.length ? experiences.slice(0, 6).map(e => `
    <div class="event">
      <div class="event-top"><strong>${esc(e.symbol || '-')}</strong>${chip(e.outcome_label || '-', actionClass(e.outcome_label))}</div>
      <div>${chip(e.signal_type || '-')}</div>
      <div class="event-reason">${esc(e.lesson || '-')}</div>
    </div>`).join('') : '<div class="empty">暂无经验</div>';
}

function renderHeat(items) {
  if (!items.length) {
    $('heatPanel').innerHTML = '<div class="empty">暂无热度</div>';
    return;
  }
  const max = Math.max(...items.map(i => num(i.score || i.heat_score || i.heat || 0)), 1);
  $('heatPanel').innerHTML = items.slice(0, 10).map(i => {
    const score = num(i.score || i.heat_score || i.heat || 0);
    const sym = i.symbol || i.token || '-';
    return `<div class="heat-row"><strong>${esc(sym)}</strong><div class="bar"><i style="width:${Math.max(4, score / max * 100)}%"></i></div><span class="muted">${score.toFixed(1)}</span></div>`;
  }).join('');
}

function renderChart(curve) {
  const canvas = $('equityChart');
  if (!canvas || curve.length < 2) return;
  if (equityChart) equityChart.destroy();
  const first = num(curve[0]);
  const last = num(curve[curve.length - 1]);
  const color = last >= first ? '#21c77a' : '#ef476f';
  equityChart = new Chart(canvas.getContext('2d'), {
    type: 'line',
    data: { labels: curve.map((_, i) => i), datasets: [{ data: curve, borderColor: color, backgroundColor: color + '22', fill: true, tension: .32, pointRadius: 0, borderWidth: 2 }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { display: false }, y: { grid: { color: '#2b303a' }, ticks: { color: '#9aa3b2' } } } }
  });
}

async function load() {
  const resp = await fetch('/api/dashboard');
  render(await resp.json());
}
async function closePos(id) {
  if (!confirm(`确认平仓 #${id}？`)) return;
  await fetch('/api/close/' + id);
  load();
}
async function closeAll() {
  if (!confirm('确认全部平仓？')) return;
  await fetch('/api/close-all');
  load();
}
async function triggerScan() {
  const btn = $('scanBtn');
  btn.disabled = true;
  btn.textContent = '扫描中';
  try { await fetch('/api/scan'); await load(); }
  finally { btn.disabled = false; btn.textContent = '手动扫描'; }
}
async function reviewDue() {
  await fetch('/api/decision-memory/review-due');
  load();
}
load();
setInterval(load, 3000);
</script>
</body>
</html>
"""


@app.get("/")
def index():
    return HTMLResponse(content=AGENT_WORKBENCH_HTML, media_type="text/html")


@app.get("/api/scan")
def api_scan():
    from scanner import Scanner
    result = Scanner().run()
    _invalidate("dashboard")
    return {"ok": True, "action": result.get("scan", {}).get("action", "unknown")}


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=8080)
