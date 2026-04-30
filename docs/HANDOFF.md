# Agent Handoff

Last updated: 2026-04-30 23:30 UTC+8

## Mission

`trading-core` is a paper-first autonomous crypto futures trading system for Binance USD-M. All phases 1-6 of the refactor plan are complete. The system runs paper trading only.

**Core principle**: Keep live trading disabled. Make every decision auditable. Learn from outcomes.

## System Architecture (Completed Pipeline)

```
social heat candidates → market_snapshot → signals.analyze → ATR risk sizing → TP pyramid execution → failure archive + adaptive weights
```

## Current Runtime

Local Windows setup:
```powershell
cd C:\Users\xaohi\Downloads\trading-core-main\trading-core-main
.\.venv\Scripts\python.exe server.py status
```

- Web UI: `http://localhost:8080`
- Daemon: stopped unless user asks to run
- Execution mode: paper only (live trading explicitly blocked)

## Important Files

### Core Modules
| File | Purpose |
|---|---|
| `scanner.py` | Main orchestrator: scan() + monitor() + run() |
| `market_snapshot.py` | Binance fapi metrics (OI, LSR, taker, depth, ATR) |
| `signals.py` | 0-100 scoring + verdict + tags + OI divergence |
| `social_heat.py` | Binance Square feed → token mentions → heat leaderboard |
| `reflection.py` | Failure archive + strategy weights + rule suggestions |
| `backtest.py` | Historical kline replay engine with TP/SL, sizing modes, equity curve |
| `executor.py` | Paper execution: ATR sizing, TP pyramid, trailing stop |
| `decision_memory.py` | Decision journal + outcome review + experience cases |
| `memory.py` | Strategy parameter evolution from closed trades |
| `risk.py` | Account-level risk checks |
| `paper_balance.py` | Virtual balance + equity + unrealized PnL |
| `realtime_monitor.py` | Second-by-second position monitoring (REST polling) |
| `web.py` | FastAPI dashboard + JSON APIs |

### Data Layer
| File | Purpose |
|---|---|
| `db/connection.py` | SQLite schema + migrations |
| `db/trades.py` | Trade CRUD + partial_close + signal recording |

### Config
| File | Purpose |
|---|---|
| `config.py` | All config: strategies, ATR, TP, heat, risk, paths |
| `state.py` | Runtime state → state.json |

### Strategies
| File | Purpose |
|---|---|
| `strategies/detectors.py` | 4 seed strategies: neg_funding, pos_funding, crash_bounce, pump |
| `strategies/environment.py` | Market environment check (BTC trend, F&G, OI, volume) |

### Tests
| File | Coverage |
|---|---|
| `tests/smoke_market_snapshot.py` | Phase 1: market snapshot |
| `tests/smoke_signals.py` | Phase 2: signal scoring |
| `tests/smoke_phase4b.py` | Phase 4B: ATR sizing + TP pyramid |
| `tests/smoke_phase5.py` | Phase 5: social heat |
| `tests/smoke_phase6.py` | Phase 6: reflection engine |
| `tests/smoke_phase7a.py` | Phase 7A: risk hardening |
| `tests/smoke_phase7e.py` | Phase 7E: backtesting engine |
| `tests/smoke_decision_memory.py` | Decision memory loop |

### Docs
| File | Purpose |
|---|---|
| `docs/REFACTOR_PLAN.md` | Phase deliverables and definition of done |
| `docs/PROJECT_STATE.md` | Current state, verified commands, all phase details |
| `docs/HANDOFF.md` | This file |
| `docs/REFERENCE_SOURCES.md` | Source mapping for connectfarm1 and ZAIJIN88 |

## Key APIs

| Endpoint | Data |
|---|---|
| `GET /api/dashboard` | Balance, equity, positions, history, signals, heat, equity curve |
| `GET /api/signals` | Strategy stats + recent signals |
| `GET /api/decision-memory` | Recent decisions + experiences |
| `GET /api/decision-memory/review-due` | Review pending decisions |
| `GET /api/decision-memory/retrieve` | Similar experience cases |
| `GET /api/heat-leaderboard` | Social heat leaderboard |
| `GET /api/failure-archive` | Failed trades with root-cause tags + tag stats |
| `GET /api/strategy-weights` | Adaptive strategy weights + rule suggestions |

## TP Pyramid (Phase 4B)

| Stage | Trigger | Action |
|---|---|---|
| TP1 | Price ≥ entry + 1.5R | Close 30%, move SL to breakeven |
| TP2 | TP1 done, price ≥ entry + 3R | Close 30% of remaining, set trailing |
| Trailing | After TP2 | Peak - ATR*2.0, updates on new highs |
| Hard SL | Price ≤ SL | Full close |

## Position Sizing (Phase 4B)

- `risk_amount = equity * RISK_PER_TRADE_PCT` (default 2%)
- `notional = risk_amount / stop_distance`
- Clamped by `POSITION_PCT` max and `MIN_NOTIONAL_USDT`
- Stop distance = ATR% * `ATR_STOP_MULTIPLIER` (default 1.5), fallback to strategy `sl_pct`

## Failure Tags (Phase 6)

14 categories grouped:
- **Entry**: `entry_not_healthy`, `entry_15m_hot`, `entry_1h_hot`, `entry_funding_hot`, `entry_lsr_hot`
- **Exit**: `oi15_reversed`, `oi1h_reversed`, `oi4h_reversed`, `buy_pressure_faded`
- **Sizing**: `sl_too_tight`, `sl_too_wide`
- **Context**: `tp1_hit_then_reversal`, `heat_declined`, `price_hit_stop` (fallback)

## Strategy Weights (Phase 6)

- Exponential decay (0.9) — recent trades count more
- Score: +1.0 for win, -0.5 for loss
- Normalized to sum 1.0, minimum 0.05 per strategy
- Used as 4th sorting criterion in scanner (after strength, score, env)

## Verification

```powershell
cd C:\Users\xaohi\Downloads\trading-core-main\trading-core-main
.\.venv\Scripts\python.exe -m py_compile config.py db\connection.py db\trades.py decision_memory.py scanner.py executor.py web.py social_heat.py reflection.py paper_balance.py realtime_monitor.py memory.py risk.py market_snapshot.py signals.py

$env:PYTHONIOENCODING='utf-8'; $env:PYTHONPATH=(Get-Location).Path; .\.venv\Scripts\python.exe tests\smoke_market_snapshot.py
# PHASE1_SMOKE_OK

$env:PYTHONIOENCODING='utf-8'; $env:PYTHONPATH=(Get-Location).Path; .\.venv\Scripts\python.exe tests\smoke_signals.py
# PHASE2_SMOKE_OK

$env:PYTHONIOENCODING='utf-8'; $env:PYTHONPATH=(Get-Location).Path; .\.venv\Scripts\python.exe tests\smoke_phase4b.py
# PHASE4B_SMOKE_OK

$env:PYTHONIOENCODING='utf-8'; $env:PYTHONPATH=(Get-Location).Path; .\.venv\Scripts\python.exe tests\smoke_phase5.py
# PHASE5_SMOKE_OK

$env:PYTHONIOENCODING='utf-8'; $env:PYTHONPATH=(Get-Location).Path; .\.venv\Scripts\python.exe tests\smoke_phase6.py
# PHASE6_SMOKE_OK

$env:PYTHONIOENCODING='utf-8'; $env:PYTHONPATH=(Get-Location).Path; .\.venv\Scripts\python.exe tests\smoke_phase7a.py
# PHASE7A_SMOKE_OK

$env:PYTHONIOENCODING='utf-8'; $env:PYTHONPATH=(Get-Location).Path; .\.venv\Scripts\python.exe tests\smoke_phase7e.py
# PHASE7E_SMOKE_OK

$env:PYTHONIOENCODING='utf-8'; $env:PYTHONPATH=(Get-Location).Path; .\.venv\Scripts\python.exe tests\smoke_decision_memory.py
# DECISION_MEMORY_SMOKE_OK
```

## Known Gaps (Not in Plan)

- WebSocket real-time layer for open positions (currently REST polling)
- Watchlist/follow-up tracking
- Full pytest unit test suite
- Multi-process architecture

## Mission Evolution

**Original Goal:** Refactor a prototype into a paper-trading system.
**Current Goal:** Build a "Living Agent" (MAKIMA/Hermes) that learns from its own experience.
- **Architecture:** Claude builds the system; Hermes analyzes and executes.
- **Efficiency:** 95% local rule execution (0 tokens). LLM is used *only* for reflection and weekly tuning.
- **Learning Loop:** Deep decision snapshots → 24h review → LLM reflection → Contextual experience retrieval → Rule evolution.

## Next Best Step

**Phase 8A: Deep Decision Snapshots.**
Current snapshots are too shallow. Next agent should:
1. Extend `decision_memory.py` to record full context: Macro (Fear & Greed/VIX), Polymarket sentiment, Order book depth, and **LLM reasoning/predicted target**.
2. Ensure these snapshots are available for the future "24h review" process.
3. This creates the "Trader's Diary" required for the agent to start learning.

**Live trading remains out of scope** until the user explicitly asks and a safety review is complete.
