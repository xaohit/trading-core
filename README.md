# Trading Core

Trading Core is an agent-assisted autonomous crypto trading system.

The goal is simple: build a system that can scan the market, find repeatable
opportunities, trade under strict risk rules, record every decision, review
outcomes, and improve over time.

The final product is Web-first. All normal operations should be available from
the Web console: scanning, paper trading, position management, decision review,
agent reflections, optimizer suggestions, backtests, settings, and safety
controls.

Agent usage is not the real-time trading engine. The system trades through local
deterministic logic. Agents such as Hermes, MAKIMA, OpenClaw, or a future custom
agent are used as the review, learning, and tuning layer.

```text
Fast loop, local:
Radar -> Strategy -> Risk -> Execution -> Monitoring -> Memory

Slow loop, agent-assisted:
Review Package -> Agent Reflection -> Lessons -> Parameter Suggestions -> Next Run
```

Current default mode is paper trading. Live trading is out of scope until a
separate safety review is completed.

## Product Definition

Trading Core is not a chatbot and not a traditional static quant bot.

It is an autonomous trading engine with an agent learning layer:

- The system handles scanning, filtering, risk checks, paper execution, and
  monitoring.
- The agent handles post-trade reflection, mistake analysis, strategy review,
  and parameter suggestions.
- The database stores decisions, outcomes, lessons, and experience cases so the
  system can reuse past knowledge.

In one sentence:

> Trading Core is an automated trading system that uses agents to learn from its
> own trading history.

## Core Loop

```text
1. Scan market candidates
2. Detect strategy signals
3. Build market snapshot and score context
4. Apply deterministic risk and quality filters
5. Rank candidates
6. Open paper trades when local rules agree
7. Monitor TP / SL / trailing stop
8. Record every open, reject, wait, and close
9. Review decisions after the configured horizon
10. Ask an agent to analyze failures and tuning opportunities
11. Store lessons and threshold suggestions
12. Feed lessons back into the next cycle
```

## Current Capabilities

### Market Radar

- Binance USD-M futures public market data
- Price, volume, 4h/24h change
- Funding rate
- Open interest and OI change
- Global and top-account long/short ratio
- Taker buy/sell flow and taker trend
- Order book depth imbalance
- ATR volatility
- Binance Square/social heat candidate pool
- Semantic event inbox for news, macro, KOL, and Polymarket-style inputs

### Strategy Signals

Current seed strategies:

| Signal | Direction | Intent |
|---|---|---|
| `neg_funding_long` | Long | Deep negative funding may indicate crowded shorts and squeeze potential |
| `pos_funding_short` | Short | Deep positive funding may indicate crowded longs and downside unwind |
| `crash_bounce_long` | Long | Sharp selloff followed by stabilization / bounce |
| `pump_short` | Short | Sharp pump followed by pullback / exhaustion |

These signals discover candidates. They do not bypass risk checks.

### Risk And Validation

- Environment filter
- Score and hard-tag rejection
- Entry veto checks
- Direction-aware taker trend veto
- Entry quality checklist
- Account-level risk checks
- Sector concentration controls
- ATR-based stop planning
- Risk/reward validation
- Conservative defaults with optional state-based threshold overrides

### Execution

- Paper trading by default
- Risk-based sizing
- ATR stop distance
- TP1 / TP2 partial take-profit
- Breakeven protection
- Trailing stop
- Manual close / close all
- Trade history and outcome records

### Memory And Learning

- Decision snapshots
- 24h / horizon-based review
- Outcome labels such as `target_hit`, `direction_correct`, `direction_wrong`,
  and `invalidated`
- Experience case archive
- Similar experience retrieval
- Agent reflection storage
- Daily reflection report
- Self-Optimizer diagnostics and threshold suggestions

## Agent Role

Agents should be used where they are strongest: reflection, explanation, and
slow-cycle improvement.

Good agent tasks:

- Explain why a trade failed
- Identify ignored signals
- Compare current failures with historical experience
- Suggest whether a threshold should be loosened or tightened
- Write lessons into the experience library
- Review whether a strategy family still has positive expectancy

Bad agent tasks:

- Bypass local risk checks
- Make every real-time trade decision with full LLM reasoning
- Change thresholds without reviewed data
- Enable live trading without a separate safety process

## Agent Tool Interface

`agent_tools.py` exposes the system to external agents:

- `get_skill_manifest()`
- `get_market_analysis(symbol)`
- `validate_trade_setup(...)`
- `record_agent_decision(...)`
- `review_due_decisions()`
- `store_reflection(...)`
- `get_daily_reflection_report()`
- `add_semantic_event(...)`
- `run_backtest(...)`
- `run_monte_carlo_analysis(...)`
- `inject_historical_experience(...)`
- `adjust_strategy_params(...)`

The interface remains useful for Hermes/OpenClaw integration, but the primary
trading loop is local and deterministic.

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
See [docs/RESTRUCTURE_PLAN.md](docs/RESTRUCTURE_PLAN.md) for the Web-first
restructure plan.

Current high-level structure:

```text
trading-core/
  main.py                 # Daemon entry point (minute loop / --once)
  scanner.py              # Core orchestrator: scan, route, execute, monitor
  strategy_router.py      # Phase 4: per-regime adaptive signal weighting
  market_regime.py        # Phase 4: trending/ranging/volatile state detection
  market_state.py         # Deprecated — redirect shim to market_regime
  market.py               # Market data: klines, tickers, funding (requests lib)
  execution/executor.py   # Paper execution and position management
  risk/risk.py            # Account and entry risk checks
  strategies/
    detectors.py          # Phase 3: trend-following signal detectors
    environment.py        # 7-factor market environment scoring
    narrative_radar.py    # Social heat / narrative-aware candidate pool
  monitor/signal_engine.py # Phase 3: standalone reference signal engine
  learning/
    self_optimizer.py     # Per-regime parameter optimization (--apply mode)
    experience_injector.py # Inject historical cases into new scans
  memory/
    decision_memory.py    # Decision snapshots, review, experience cases
  reflection.py           # Strategy weighter + failure archive
  agent_tools.py          # External agent review/tool interface
  agent_decision.py       # Local decision gate for paper daemon mode
  config.py               # Configuration (API keys via env vars)
  db/                     # SQLite: trades, signals, snapshots, cases, klines
  deep_review.py          # Hermes-authored post-trade deep analysis
  docs/                   # Architecture, plans, handoff notes
  tests/                  # Smoke tests
```

## Quick Start

```bash
git clone https://github.com/xaohit/trading-core.git
cd trading-core
python -m venv .venv
```

Windows:

```powershell
.\.venv\Scripts\pip install -r requirements.txt
python server.py restart
python server.py status
```

Linux/macOS:

```bash
source .venv/bin/activate
pip install -r requirements.txt
python server.py restart
python server.py status
```

Web UI:

```text
http://localhost:8080
```

## Verification

```bash
python -m py_compile config.py scanner.py decision_pipeline.py agent_decision.py decision_memory.py executor.py backtest.py agent_tools.py self_optimizer.py

python tests/smoke_decision_pipeline.py
python tests/smoke_decision_provider.py
python tests/smoke_decision_memory.py
python tests/smoke_agent_gate.py
python tests/smoke_phase7a.py
```

## Current Status

Working:

- Paper-trading autonomous loop (daemon + --once)
- Phase 3 trend-following detectors (MA + ADX + funding rate)
- Phase 4 adaptive strategy router (per-regime weighting from state.json)
- Market regime detection (trending / ranging / volatile)
- Risk-based sizing with ATR stop distance
- TP1/TP2 partial take-profit + trailing stop
- Decision memory and experience archive (838 snapshots, 847 cases)
- Deep review by Hermes agent (per-trade analysis, scoring, improvement tags)
- Agent-facing review/reflection tools
- Self-Optimizer diagnostics with --apply mode

Still needs work:

- More paper-trading samples (8 closed trades — need 10 for first evolution trigger)
- Backtesting to validate parameter changes
- Daemon heartbeat monitoring
- Profit attribution reports
- Live-trading safety gate
- Expand tests beyond smoke coverage

## Safety

This project is for research, paper trading, and system development. It does not
guarantee profit and should not be treated as investment advice.

Do not enable live trading until the project has:

- Sufficient paper-trading sample size
- Positive expectancy after fees and estimated slippage
- Maximum drawdown controls
- Daily loss limit
- Kill switch
- Order idempotency
- Exchange error handling
- Dry-run reconciliation
- Small-capital gray release plan
