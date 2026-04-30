# Trading Core Refactor Plan

> Project-level delivery rule: every completed phase must leave the repo in a runnable state and update `docs/PROJECT_STATE.md`. No hidden context dependency.

## Goal
Upgrade `trading_core` from connectfarm1-style rule prototype into a ZAIJIN88-inspired market-aware paper trading system.

## Architecture Direction

Current:
```text
all tickers → 4 seed detectors → simple environment score → paper open → fixed SL/TP
```

Target:
```text
candidate pool
  → market_snapshot(symbol)
  → signals.analyze(snapshot, heat)
  → risk + ATR sizing
  → paper execution with TP pyramid
  → failure archive + adaptive strategy weights
```

## Phase Deliverables

### Phase 1 — Market Snapshot Layer
Files:
- Create `market_snapshot.py`
- Optionally extend `market.py` with safe raw endpoint helpers only if needed

Definition of done:
- `get_market_snapshot(symbol)` returns stable dict for active USD-M futures symbols.
- It never raises on missing Binance data; missing fields become `None` or `0`.
- Smoke test passes for `BTCUSDT` and one current open position symbol.

### Phase 2 — Signal Scoring Layer
Files:
- Create `signals.py`
- Create `tests/test_signals.py` if pytest available, otherwise smoke script in docs.

Definition of done:
- `signals.analyze(snapshot, heat_score=0)` returns `{score, verdict, tags, notes, oi_divergence}`.
- No dependency on web/dashboard.

### Phase 3 — Scanner Integration
Files:
- Modify `scanner.py`
- Modify `db/trades.py` / `db/connection.py` only if signal_history schema needs extra fields
- Modify `web.py` for display if safe

Definition of done:
- Daemon runs one full scan without crash.
- `signal_history` contains new score/verdict information.
- Existing fixed strategies still work as fallback.

### Phase 4 — Execution Upgrade
Files:
- Modify `executor.py`
- Modify `scanner.py::monitor`
- Potential DB migration for partial closes/trailing state

Definition of done:
- TP1/TP2/trailing stop work in paper mode.
- Existing manual close still works.

### Phase 5 — Social Heat Layer
Files:
- Create `social_heat.py` or `square_scraper.py`
- Add process only after Phase 1-4 stable

Definition of done:
- Generates leaderboard independent of trading.
- Scanner can optionally use leaderboard candidates.

### Phase 6 — Reflection Engine
Files:
- Create `reflection.py`
- Modify `scanner.py` for strategy weights
- Add `failure_archive` table to `db/connection.py`

Definition of done:
- Stopped-out trades are auto-archived with root-cause failure tags.
- Strategy weights are computed from recent performance and used in scanner.
- Rule suggestions are generated from frequent failure tags.

### Phase 7A — Risk Hardening (HIGH PRIORITY) ✅ DONE
Files:
- Modify `risk.py`
- Modify `config.py`
- Modify `scanner.py` scan() entry gate
- `tests/smoke_phase7a.py`

Definition of done:
- Sector concentration limit: max 2 positions per sector (majors/l2/meme/ai/defi/alt_l1) ✅
- Daily loss circuit breaker: stop trading when daily loss > 5% equity ✅
- Daily trade limit: max 15 opens per day ✅
- Cooldown after loss: 30 min cooldown after any SL hit ✅
- Entry quality gate: 7-item checklist + hard vetoes before opening ✅
- `tests/smoke_phase7a.py` prints `PHASE7A_SMOKE_OK` ✅

### Phase 7B — Web Frontend Update (MEDIUM PRIORITY)
Files:
- Modify `web.py` HTML dashboard

Definition of done:
- Dashboard shows social heat leaderboard table
- Dashboard shows failure archive tag frequency chart
- Dashboard shows strategy weights ranking
- Dashboard shows rule suggestions when available
- All existing sections still render correctly

### Phase 7C — WebSocket Real-time Layer (MEDIUM PRIORITY)
Files:
- Create `realtime_ws.py`
- Modify `realtime_monitor.py` or replace
- Add WebSocket to `server.py` if running as daemon

Definition of done:
- Open positions subscribed to `@bookTicker` + `@aggTrade` streams
- Database updated with second-by-second prices
- TP/SL checks use real-time prices instead of REST polling
- Auto-reconnect on disconnect

### Phase 7D — Watchlist System (LOW PRIORITY)
Files:
- Create `watchlist.py`
- Add `watchlist` / `watchlist_entries` / `watchlist_followups` tables
- Modify `web.py` for watchlist UI
- Modify `scanner.py` to allow watchlist candidates

Definition of done:
- User can add/remove symbols to watchlist via API
- Watchlist tracks anchor price and follow-up PnL
- Auto-archive loss samples when drawdown ≤ -10%
- Dashboard shows watchlist table with live prices

### Phase 7E — Backtesting Engine (HIGH PRIORITY) ✅ DONE
Files:
- Create `backtest.py`
- Create `tests/smoke_phase7e.py`

Definition of done:
- Can replay historical klines from Binance fapi ✅
- Runs all 4 seed strategies through historical data ✅
- Produces: total trades, win rate, avg pnl, max drawdown, equity curve, per-strategy stats ✅
- Compares ATR sizing vs fixed % sizing ✅
- CLI: `python backtest.py --symbol BTCUSDT --start 2025-01-01 --end 2025-04-01` ✅
- `tests/smoke_phase7e.py` prints `PHASE7E_SMOKE_OK` ✅

### Phase 8 — Agent Learning Loop (The "Living System")

**Core Vision:**
A trading system that evolves from "novice" to "expert" by continuously recording decisions, reflecting on outcomes, and applying learned experience to future contexts. 
- **Efficiency:** 95% local rule execution (0 tokens), LLM only for reflection and weekly rule review.
- **Growth Path:** Week 1 (basic logic) → Month 1 (experience-based filtering) → Month 6 (context-aware "expert").

**Sub-phases:**
1. **8A: Deep Decision Snapshots ✅ DONE** 
   - Extended `decision_snapshots` table: Added `macro_context`, `market_state`, `agent_reasoning`.
   - Created `agent_tools.py`: Exposes `get_market_analysis`, `record_agent_decision`, `review_due_decisions` as clean APIs for the agent.
   - Created `market_state.py`: Classifies market into Trend/Range/Volatile using ADX/ATR.
   - Scanner now captures full context for every decision.

2. **8B: Backtest-to-Experience Injection ✅ DONE** 
   - Connect `backtest.py` results to the experience library.
   - Hermes analyzes historical K-lines and macro nodes to generate an initial "Experience Base".
   - Files: `backtest.py`, `experience_injector.py`

3. **8C: 24h Review & LLM Reflection ✅ Framework DONE**
   - Automatic 24h (or dynamic) check: prediction vs reality. 
   - On failure: Hermes analyzes "what was missed" (e.g., Polymarket divergence, lagging signals).
   - Archives dynamic LLM-generated lessons with tags.
   - Files: `agent_framework.py`

4. **8D: Contextual Experience Retrieval ✅ Framework DONE**
   - Top 3-5 experiences injected into new decisions, but **weighted by market state match** (e.g., Trend vs Range, Volatility level).
   - Avoids blind copy-paste of lessons across different market regimes.

5. **8E: Feedback Loop & Rule Adjustment ✅ Framework DONE**
   - Hermes feedback.log → Weekly automated rule tuning.
   - "If suggestion led to >55% wins, adjust threshold automatically."
   - Files: `agent_framework.py`, `agent_tools.py` (adjust_strategy_params)

### Phase 9 — Quant Rigor & Safety (Professional Standards)

**Goal:** Bridge the gap between "AI Prototype" and "Institutional Grade" by adding mathematical rigor and hard safety constraints.

1. **9A: Backtest Realism (Cost Modeling) ✅ IN PROGRESS**
   - Add Transaction Costs (0.04% Taker fees x2).
   - Add Slippage modeling (1 tick / order book depth simulation).
   - Account for Funding Rate costs in holding periods > 8h.
   - Files: `backtest.py`

2. **9B: Agent Guardrails (Hard Constraints)**
   - LLM "Safety Sandbox": LLM can suggest confidence adjustments, but *must* pass `risk.py` deterministic checks.
   - Hard caps: Max leverage (absolute), Max position size (absolute), Emergency Blacklists.
   - Files: `risk.py`, `agent_framework.py`

3. **9C: Portfolio Risk Math**
   - Correlation monitoring (don't hold correlated longs).
   - Volatility targeting (normalize position sizes).
   - Files: `portfolio.py`

### Phase 7 Priority Order
Execute in order: 7A → 7E → 8A → 8B → 8C → 8D → 8E
Phase 7A/7E provide the safety and validation foundation. Phase 8 is the core "learning" evolution.
Phase 7B/7C/7D are independent maintenance/UI features.

## Verification Commands

Always run after changes:
```bash
cd /Users/xaohit/.hermes/trading_core
/Users/xaohit/.hermes/hermes-agent/venv/bin/python -m py_compile *.py db/*.py strategies/*.py
/Users/xaohit/.hermes/hermes-agent/venv/bin/python server.py status
curl -s --max-time 10 http://localhost:8080/api/dashboard | /Users/xaohit/.hermes/hermes-agent/venv/bin/python -m json.tool >/dev/null
```

Phase 1 smoke:
```bash
/Users/xaohit/.hermes/hermes-agent/venv/bin/python - <<'PY'
from market_snapshot import get_market_snapshot
for sym in ['BTCUSDT', 'PRLUSDT']:
    s = get_market_snapshot(sym)
    print(sym, s['price'], s['atr_pct'], s['taker_ratio'])
    assert s['symbol'] == sym
    assert s['price'] >= 0
PY
```

## Rules
- Keep paper mode first.
- Do not add push notifications.
- Do not store API keys in docs.
- No giant hidden rewrites; each phase must be independently understandable.
