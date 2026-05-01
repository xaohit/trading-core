# Trading Core Project State

Last updated: 2026-04-30 21:50 UTC+8

## Mission
Build xaohit's autonomous crypto trading system from prototype into a project-level, recoverable, testable system.
**Ultimate Goal: A living Agent (MAKIMA/Hermes) that learns from its own experience.** 
1. Every decision is recorded with full context (macro, sentiment, reasoning).
2. Predictions are reviewed after 24h; failures trigger LLM-driven reflection.
3. Experience is contextually retrieved to inform future decisions.
4. Rules evolve automatically based on feedback loops.

**Architecture:** Claude builds the system, Hermes executes/analyzes. 95% local execution (0 tokens), LLM only for reflection and weekly tuning.

Primary references:
1. `https://connectfarm1.com/` → `Futures + Alpha Autonomous Trading v1` as seed strategy prototype.
2. `https://github.com/ZAIJIN88/binance-square-monitor` → mature architecture reference for market snapshot, scoring, risk, paper trading, and dashboard.

## Current Runtime
Project root: `/Users/xaohit/.hermes/trading_core`

Run/operate:
```bash
cd /Users/xaohit/.hermes/trading_core
/Users/xaohit/.hermes/hermes-agent/venv/bin/python server.py status
/Users/xaohit/.hermes/hermes-agent/venv/bin/python server.py restart
```

Current services:
- Web UI: `http://localhost:8080`
- Daemon: `main.py`, scan interval 300s
- Database: `/Users/xaohit/.hermes/trading_core/trading_core.db`
- State: `/Users/xaohit/.hermes/trading_core/state.json`

## Current Implemented System
- Paper trading only.
- Binance USD-M futures public data via proxy `socks5h://localhost:7897`.
- 4 seed strategies:
  - `neg_funding_long`
  - `pos_funding_short`
  - `crash_bounce_long`
  - `pump_short`
- Environment check: BTC 24h change, Fear & Greed, OI size, 24h volume, signal strength.
- Basic SL/TP per signal.
- Web dashboard: balance/equity/open positions/history/stats/manual close/scan.
- Adaptive params: every 10 closed trades, per-strategy >=5 samples, evolves SL/TP into `state.json[evolved_params]`.

## Known Fixes Already Applied
- Binance error dicts are no longer treated as list rows in `market.py`.
- `State.daily_loss_limit()` only triggers on net negative daily pnl, not `abs(pnl)`.
- `risk.py::check_account_risk()` nested relative import has absolute fallback.
- `web.py /api/dashboard` returns `evolved_params` correctly.
- Windows runtime compatibility added: UTF-8 output, process start/status fixes, explicit subprocess decoding.
- `requirements.txt` added for local setup.

## Known Gaps
The system is still v0.4. Missing production-grade components from ZAIJIN88:
- WebSocket real-time layer for open positions (currently REST polling).
- Sector concentration risk controls (SECTOR_MAP + correlated position limits).
- Entry quality gate (7-item checklist + hard vetoes from ZAIJIN88).
- Backtesting system (no historical K-line replay).
- Watchlist/follow-up tracking with loss archive threshold.
- Multi-process architecture (worker/market_realtime/web/auto_trader).
- Full pytest test suite (currently smoke tests only).

Completed from ZAIJIN88:
- Phase 1 market snapshot data layer exists in `market_snapshot.py`: OI history, global/top LSR, taker flow, depth imbalance, ATR, multi-timeframe price changes.
- Phase 2 scoring layer exists in `signals.py`: 0-100 score, verdict, tags, notes, OI divergence.
- Phase 3 scanner integration records snapshot/scoring metadata in `signal_history` and trade `pre_analysis`.
- Decision Memory Loop first slice exists in `decision_memory.py`: decision snapshots, outcome reviews, and experience cases.
- Experience retrieval is wired: similar cases can be fetched by symbol, signal type, and tags, then injected into new decision context.

## Refactor Roadmap

### Phase 0 — Project-level handoff guardrails
Deliverables:
- `docs/PROJECT_STATE.md` (this file)
- `docs/REFACTOR_PLAN.md`
- each phase must update this file with status, files touched, commands, result

### Phase 1 — Market Snapshot Layer ✅ DONE
Goal: port ZAIJIN88-style futures metrics without adding Playwright/social scraping yet.

Files delivered:
- `market_snapshot.py`
- `tests/smoke_market_snapshot.py`

Expose:
```python
from market_snapshot import get_market_snapshot
snapshot = get_market_snapshot("BTCUSDT")
```

Snapshot fields:
- price
- change_15m / change_1h / change_4h / change_24h
- oi
- oi_15m_change / oi_1h_change / oi_4h_change
- funding_rate
- global_lsr
- top_lsr
- taker_ratio
- taker_trend_pct
- depth_bid_usd_1pct / depth_ask_usd_1pct / depth_imbalance
- atr_pct
- quote_volume_24h

Verified:
```bash
PYTHONPATH=/Users/xaohit/.hermes/trading_core \
/Users/xaohit/.hermes/hermes-agent/venv/bin/python tests/smoke_market_snapshot.py
# PHASE1_SMOKE_OK
```

Observed sample output:
```text
BTCUSDT price=76349.9 atr=0.5421 taker=0.8091 oi1h=-0.1447 depth=-15.97
PRLUSDT price=0.2875 atr=2.65 taker=1.2295 oi1h=-0.9357 depth=23.98
```

### Phase 2 — Scoring Layer ✅ DONE
Created:
- `signals.py`
- `tests/smoke_signals.py`

Expose:
```python
from signals import analyze
result = analyze(snapshot, heat_score=0)
```

Output:
- score 0-100
- verdict
- tags
- notes
- oi_divergence

Verified:
```bash
PYTHONPATH=/Users/xaohit/.hermes/trading_core \
/Users/xaohit/.hermes/hermes-agent/venv/bin/python tests/smoke_signals.py
# PHASE2_SMOKE_OK
```

Observed sample output:
```text
BTCUSDT 64.0 🎯 值得留意 ['bid_depth_strong', 'buy_pressure_falling', 'funding_normal', 'liquid', 'lsr_normal', 'oi_high']
PRLUSDT 74.0 ✅ 看起来健康 ['bid_depth_strong', 'buy_pressure_rising', 'funding_negative', 'lsr_normal', 'oi_ok', 'top_less_bullish']
```

### Phase 3 — Integrate Snapshot + Scoring into Scanner ✅ DONE
Modify:
- `scanner.py`
- `strategies/environment.py` or replace with `signals.py`
- `web.py` API includes latest snapshot/scoring fields

Acceptance:
- daemon scans without crash
- `signal_history` records score/verdict/tags
- dashboard still works

Delivered:
- `scanner.py` calls `market_snapshot.get_market_snapshot()` and `signals.analyze()` for strategy candidates.
- `db/connection.py` migrates `signal_history` with `verdict`, `tags`, `notes`, `snapshot_json`, and `analysis_json`.
- `db/trades.py::record_signal()` persists the new scoring context.
- `executor.py` stores score/verdict/tags/snapshot/analysis in opened trade `pre_analysis`.
- `web.py /api/signals` returns structured recent signal objects; `/api/dashboard` includes `scanner.latest_signals`.

Verified on Windows:
```powershell
.\.venv\Scripts\python.exe -m py_compile db\connection.py db\trades.py scanner.py executor.py web.py
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONPATH=(Get-Location).Path; .\.venv\Scripts\python.exe tests\smoke_signals.py
# PHASE2_SMOKE_OK
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONPATH=(Get-Location).Path; .\.venv\Scripts\python.exe tests\smoke_market_snapshot.py
# PHASE1_SMOKE_OK
```

Runtime:
- Web UI: running at `http://localhost:8080`
- Daemon: stopped

### Phase 4A — Decision Memory Loop ✅ FIRST SLICE DONE
Modify:
- `decision_memory.py`
- `db/connection.py`
- `scanner.py`
- `web.py`
- `tests/smoke_decision_memory.py`

Delivered:
- `decision_snapshots` table records structured decision journals.
- `decision_outcomes` table records review results after the configured horizon.
- `experience_cases` table archives compact lessons.
- Scanner records `opened`, `score_reject`, `risk_reject`, and `env_reject` decisions.
- Scanner retrieves Top 3 similar experience cases and stores them in each new decision context.
- `/api/decision-memory` returns recent decisions and experiences.
- `/api/decision-memory/review-due` reviews due pending decisions.
- `/api/decision-memory/retrieve` returns similar experience cases for a symbol/signal/tags query.

Verified:
```powershell
.\.venv\Scripts\python.exe -m py_compile config.py db\connection.py decision_memory.py scanner.py web.py tests\smoke_decision_memory.py
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONPATH=(Get-Location).Path; .\.venv\Scripts\python.exe tests\smoke_decision_memory.py
# DECISION_MEMORY_SMOKE_OK
```

Important:
- This layer records, reviews, and retrieves decision experience only.
- It does not mutate strategy rules yet.
- Adaptive rules should wait until repeated similar lessons exist.

### Phase 4B — ATR Risk + TP Pyramid ✅ DONE
Modified:
- `executor.py`: ATR-based stop distance, R-multiple TP1/TP2/trailing levels, risk-based sizing, `check_tp_levels()`, `update_trailing_stop()`
- `scanner.py`: monitor() rewritten for TP pyramid (partial close at TP1→breakeven SL, TP2→trailing, then full close)
- `realtime_monitor.py`: same TP pyramid logic for second-by-second monitoring
- `db/connection.py`: added `tp1_price`, `tp1_done`, `tp2_price`, `tp2_done`, `trailing_stop`, `remaining_pct`, `breakeven_done`, `initial_r`, `stop_distance`, `atr_pct_at_entry` columns
- `db/trades.py`: `partial_close()` method for TP stage exits
- `paper_balance.py`: unrealized PnL accounts for `remaining_pct`
- `web.py`: dashboard shows TP stages, trailing stop, and remaining position %
- `config.py`: `ATR_STOP_MULTIPLIER`, `RISK_PER_TRADE_PCT`, `TP1_R_MULTIPLE`, `TP2_R_MULTIPLE`, `TP1_CLOSE_PCT`, `TP2_CLOSE_PCT`, `TRAILING_STOP_ATR_MULT`, `ATR_LOOKBACK`, `MIN_NOTIONAL_USDT`
- `tests/smoke_phase4b.py`: ATR sizing, TP trigger long/short, trailing update, partial close DB ops, ATR fallback

Acceptance:
- open trade stores R/TP1/TP2/trailing state ✅
- monitor can partial close paper position ✅
- stops move to breakeven after TP1 ✅
- trailing stop updates on price peak ✅
- `tests/smoke_phase4b.py` prints `PHASE4B_SMOKE_OK` ✅
- existing smoke tests still pass ✅

TP pyramid behavior:
- TP1 (1.5R): close 30%, move SL to breakeven
- TP2 (3R): close 30% of original (of remaining), keep trailing
- Final 40%: trailing stop at peak - ATR*2.0
- Hard SL: ATR*1.5 from entry (fallback to strategy sl_pct if ATR unavailable)

Position sizing:
- risk_amount = equity * RISK_PER_TRADE_PCT (default 2%)
- notional = risk_amount / stop_distance
- clamped by POSITION_PCT max and MIN_NOTIONAL_USDT

### Phase 5 — Social Heat Candidate Pool ✅ DONE
Created:
- `social_heat.py`: Binance Square feed fetcher (no Playwright), token mention extraction (`$TOKEN`/`#TOKEN`), time-decayed heat scoring, author/content dedup, 15m leaderboard
- `config.py`: `SOCIAL_HEAT_ENABLED`, `HEAT_WINDOW_MINUTES`, `HEAT_HALF_LIFE_HOURS`, `HEAT_TOP_N`, `HEAT_CANDIDATE_N`
- `scanner.py`: `scan()` uses heat candidates when available, falls back to all-market scan
- `web.py`: `/api/heat-leaderboard` endpoint, dashboard includes `heat_leaderboard` data
- `tests/smoke_phase5.py`: token extraction, author filter, heat computation, time decay, same-author downweight, leaderboard API

Acceptance:
- can produce 15m heat leaderboard ✅
- scanner can use heat candidates before fallback to all-market scan ✅
- `tests/smoke_phase5.py` prints `PHASE5_SMOKE_OK` ✅
- existing smoke tests still pass ✅

Heat scoring mechanics (ZAIJIN88-inspired):
- Score = likes*1 + comments*3 + shares*5, with exponential time decay (half-life 15min)
- Same author 3rd+ post on same token → 0.25x downweight
- Duplicate content signature → 0.35x downweight
- Bot/marketing filter: default username patterns + low engagement threshold
- Big V (≥100K followers) always passes
- EXCLUDED_TOKENS filter removes noise (USDT, BUSD, GM, etc.)

Scanner integration:
- When `SOCIAL_HEAT_ENABLED=True` (default), scanner tries heat leaderboard first
- If heat candidates available → only scans those symbols
- If heat API unavailable → falls back to full market scan
- Scan results include `heat_used` flag and `heat_candidates` count

### Phase 6 — Reflection Engine ✅ DONE
Created:
- `reflection.py`: Three components — `FailureArchive` (auto-tags stopped-out trades with root-cause analysis), `StrategyWeighter` (dynamic strategy prioritization based on recent performance), `RuleReflector` (suggests hard-entry thresholds from frequent failure tags)
- `db/connection.py`: `failure_archive` table with 12 columns
- `scanner.py`: `scan()` uses adaptive strategy weights in signal sorting; `monitor()` auto-archives failures with tags
- `web.py`: `/api/failure-archive` and `/api/strategy-weights` endpoints
- `tests/smoke_phase6.py`: 5 test cases (failure tagging, archive, strategy weights, rule reflection, no false tags)

Acceptance:
- stopped-out trades are archived with root-cause failure tags ✅
- strategy weights are computed from recent performance and used in scanner sorting ✅
- rule suggestions are generated from frequent failure tags ✅
- `tests/smoke_phase6.py` prints `PHASE6_SMOKE_OK` ✅
- existing smoke tests still pass ✅

Failure tags (14 categories):
- Entry: `entry_not_healthy`, `entry_15m_hot`, `entry_1h_hot`, `entry_funding_hot`, `entry_lsr_hot`
- Exit: `oi15_reversed`, `oi1h_reversed`, `oi4h_reversed`, `buy_pressure_faded`
- Sizing: `sl_too_tight`, `sl_too_wide`
- Context: `tp1_hit_then_reversal`, `heat_declined`, `price_hit_stop` (fallback)

Strategy weights:
- Exponential decay weighting: recent trades count more (decay factor 0.9)
- Score: +1.0 for win, -0.5 for loss (risk-adjusted)
- Normalized to sum to 1.0, minimum weight 0.05 per strategy
- Used as 4th sorting criterion in scanner (after strength, score, env)

Rule reflection:
- Tags appearing ≥3 times trigger suggestions
- Each suggestion includes current rule, suggested action, and confidence score
- Suggestions can be applied to state.json for future rule enforcement

### Phase 7A — Risk Hardening ✅ DONE
Modified:
- `config.py`: Added `MAX_DAILY_TRADES` (15), `COOLDOWN_AFTER_LOSS_MINUTES` (30), `SECTOR_MAX_CONCENTRATION` (2), `ENTRY_QUALITY_MIN_PASSED` (4), `ENTRY_QUALITY_MIN_SCORE` (50)
- `risk.py`: `check_account_risk()` now uses config params instead of hardcoded values; `SECTOR_MAP` already existed with 6 sectors (majors/l2/meme/ai/defi/alt_l1)
- `scanner.py`: Added `_entry_quality_veto()` with 7 hard vetoes; added entry quality gate (7-item checklist + quality score threshold) before risk check; records `entry_veto` and `quality_reject` actions

Acceptance:
- Sector concentration limit: max 2 positions per sector ✅
- Daily loss circuit breaker: configured via MAX_DAILY_LOSS_PCT ✅
- Daily trade limit: MAX_DAILY_TRADES (15) ✅
- Cooldown after loss: COOLDOWN_AFTER_LOSS_MINUTES (30) ✅
- Entry quality gate: 7-item checklist + hard vetoes ✅
- `tests/smoke_phase7a.py` prints `PHASE7A_SMOKE_OK` ✅
- All existing smoke tests still pass ✅

Entry quality gate (7 items, min 4 pass):
1. Score ≥ 55
2. 15m change ∈ [-1.5%, 2.0%]
3. 1h change matches direction
4. OI 15m increasing
5. OI 1h increasing
6. Taker ratio ∈ [0.7, 1.5]
7. Funding rate compatible with direction

Hard vetoes (any one → reject):
- Verdict is "过热预警"
- |4h change| > 25%
- |24h change| > 50%
- |funding| ≥ 0.05%
- Retail LSR ≥ 1.7
- Taker ratio ≥ 1.8
- Taker trend ≤ -5%

### Phase 7E — Backtesting Engine ✅ DONE
Created:
- `backtest.py`: K-line replay engine with CLI, historical snapshot builder, 4-strategy signal detection, TP pyramid execution, equity curve tracking
- `tests/smoke_phase7e.py`: 6 test cases (snapshot building, basic backtest, multi-symbol, ATR vs fixed comparison, equity curve, max positions)

Acceptance:
- Can fetch historical klines from Binance fapi ✅
- Runs 4 seed strategies through historical data ✅
- Produces: total trades, win rate, avg pnl, max drawdown, equity curve, per-strategy stats ✅
- ATR sizing vs fixed % sizing comparison mode ✅
- CLI: `python backtest.py --symbol BTCUSDT --start 2025-01-01 --end 2025-04-01` ✅
- Support params override (`--sizing`, `--atr-multiplier`, `--risk-pct`, `--leverage`) ✅
- `tests/smoke_phase7e.py` prints `PHASE7E_SMOKE_OK` ✅
- All existing smoke tests still pass ✅

Backtest engine features:
- Fetches historical klines (1m/5m/15m/1h/4h) for any USD-M symbol
- Builds simplified market snapshots from kline data (price, ATR, changes)
- 4-strategy signal detection: crash bounce, pump short, momentum long, mean reversion short
- TP pyramid execution: TP1 (partial close), TP2 (partial close), trailing stop, hard SL
- Position sizing: ATR mode (risk-based) or fixed mode
- Tracks equity curve, max drawdown, per-strategy and per-symbol stats
- Outputs JSON report with `--output results.json`

### Phase 8 — Agent Learning Loop (The "Living System")

**Vision:**
> "A system that grows smarter with every trade. Not static rules, but a dynamic 'trader's diary' that learns to distinguish market contexts."

**Phase 8A: Deep Decision Snapshots (✅ DONE)**
- **Extended Schema:** `decision_snapshots` now captures `macro_context` (BTC trend, FGI), `market_state` (Trend/Range/Volatile via ADX/ATR), and `agent_reasoning`.
- **Agent Interface:** `agent_tools.py` exposes the system as a clear set of tools for Hermes/OpenClaw.
- **Scanner Integration:** Scanner now records market state and macro context for every decision.

**Phase 8B: Backtest-to-Experience Injection (✅ DONE)**
- **Experience Injector:** Created `experience_injector.py`. It runs a backtest, filters significant trades, and inserts them into the `experience_cases` table as "lessons".
- **Agent Tool:** Added `inject_historical_experience` to `agent_tools.py`, allowing Hermes to "read the history books" before starting.
- **Workflow:** The system can now turn raw historical data into actionable wisdom (e.g., "Crash bounce on BTC often fails in this volatility range").

**Phase 8C: 24h Review & LLM Reflection (✅ Framework DONE)**
- **Automated Routine:** Added `daily_reflection_routine()` to `agent_framework.py`. It automatically runs `review_due_decisions()`.
- **LLM Handoff:** If a decision failed, it generates a specific `Reflection Prompt` containing the exact Market State and Macro Context, ready for Hermes to analyze.

**Phase 8D: Contextual Experience Retrieval (✅ Framework DONE)**
- **Smart Retrieval:** The `get_market_analysis` tool now automatically appends `relevant_experiences` based on the symbol and signal type.
- **Market State Filtering:** The system classifies market into Trend/Range/Volatile (`market_state.py`) and includes this in the context, allowing the Agent to weigh experiences differently.

**Phase 8E: Feedback Loop & Rule Adjustment (✅ Framework DONE)**
- **Evolution Routine:** Added `weekly_evolution_routine()` to `agent_framework.py`. It analyzes recent wins/losses and triggers `evolve_rules()`.
- **Self-Tuning:** This function is the place where Hermes can programmatically update thresholds (e.g., `min_entry_score`) based on statistical feedback.

## Current Next Step
**Phase 9: Deployment & Tuning.**
- The system is now structurally complete (Phases 1-8).
- Hermes should run `inject_historical_experience` to build the base memory.
- Start the Agent in simulation mode (`agent_framework.py`) to accumulate data.

## 2026-05-01 Stabilization Pass

Status:
- Phase 8/9 agent files now compile and import.
- Database migrations are idempotent for existing installs.
- Closed-trade learning no longer points at missing `strategy_evolution` columns.
- Agent reflections can now be persisted through `store_reflection()` into `experience_cases`.
- Agent tool helpers support both package imports and root-directory script execution.

Verification:
- `py_compile` passed for core, web, backtest, memory, Agent, Monte Carlo, market-state, and TA checker modules.
- Smoke tests passed: market snapshot, signals, phase4b, phase5, phase6, phase7a, phase7e, decision memory.
- Agent import smoke passed: `agent_tools`, `agent_framework`, `main_agent`.

Important caveat:
- The Agent learning architecture is now coherent at the interface/storage level, but Hermes is not yet wired as a real API client and `MakimaAgent.make_decision()` remains a placeholder.

## 2026-05-01 Agent Gate Alignment

Implemented:
- Added `agent_decision.py` with `AgentDecisionGate`.
- Updated `scanner.py` so strategy rules produce candidates, but the chosen candidate must pass Agent approval before execution.
- Agent approval considers base score, signal strength, retrieved experience outcomes, and overheated/crowded tags.
- Added local TA/RR enforcement before paper execution.
- Added `agent_reject` to decision journaling so rejected Agent decisions become reviewable memory.
- Added Agent decision context to trade `pre_analysis`.
- Added `tests/smoke_agent_gate.py`.

Design intent:
- Preserve the user's MAKIMA/Hermes direction: rules filter the market, Agent judgment decides, local hard rules enforce safety, every decision is journaled for future review.
- Hermes is still not wired as the live decision provider. The current gate is deterministic and local so the system remains runnable without token spend.

Verified:
- `py_compile`: `config.py`, `scanner.py`, `executor.py`, `agent_decision.py`, `agent_tools.py`, `agent_framework.py`, `main_agent.py`, `ta_checker.py`
- `tests/smoke_agent_gate.py`: `AGENT_GATE_SMOKE_OK`
- `tests/smoke_phase7a.py`: `PHASE7A_SMOKE_OK`
- `tests/smoke_decision_memory.py`: `DECISION_MEMORY_SMOKE_OK`
- `tests/smoke_phase7e.py`: `PHASE7E_SMOKE_OK`

## 2026-05-01 Decision Pipeline Refactor

Implemented:
- Added `decision_pipeline.py`.
- Moved pre-Agent filtering out of `scanner.py` into `DecisionPipeline`.
- Scanner now has clearer responsibilities: candidate discovery, scoring context attachment, pipeline call, ranking, Agent gate, execution, journaling.
- Added `tests/smoke_decision_pipeline.py`.

Decision boundary:
- `DecisionPipeline`: deterministic pre-Agent quality/risk screening.
- `AgentDecisionGate`: experience-aware final trade/wait judgment.
- `ta_checker`: hard R/R and market-structure guard.
- `Executor`: paper execution and position management only.

Verified:
- Full core `py_compile` passed including `decision_pipeline.py`.
- `tests/smoke_decision_pipeline.py`: `DECISION_PIPELINE_SMOKE_OK`
- `tests/smoke_agent_gate.py`: `AGENT_GATE_SMOKE_OK`
- `tests/smoke_phase4b.py`: `PHASE4B_SMOKE_OK`
- `tests/smoke_phase5.py`: `PHASE5_SMOKE_OK`
- `tests/smoke_phase6.py`: `PHASE6_SMOKE_OK`
- `tests/smoke_phase7a.py`: `PHASE7A_SMOKE_OK`
- `tests/smoke_phase7e.py`: `PHASE7E_SMOKE_OK`
- `tests/smoke_decision_memory.py`: `DECISION_MEMORY_SMOKE_OK`
## Current Next Step
**Phase 9: Deployment & Tuning.**
- The system is now structurally complete (Phases 1-8).
- Hermes should run `inject_historical_experience` to build the base memory.
- Start the Agent in simulation mode (`agent_framework.py`) to accumulate data.
