# Architecture

Trading Core is an autonomous trading engine with an agent-assisted learning
layer.

The trading engine runs locally. It scans, filters, validates, executes paper
trades, monitors positions, records outcomes, and reviews decisions. Agents are
not required in the fast trading loop. They are used in the slow learning loop:
reflection, diagnosis, tuning suggestions, and strategy review.

The final product surface is the Web console. Scripts and tools can remain for
development, but normal operation should happen in the browser: engine control,
radar review, positions, decisions, memory, agent reflections, optimizer
suggestions, backtests, settings, and safety controls.

## First Principle

The project exists to test whether a repeatable, risk-controlled trading edge can
be found and improved over time.

```text
Profit system = market edge + risk discipline + reliable execution + learning loop
```

The architecture should stay simple:

```text
Radar -> Strategy -> Risk -> Execution -> Memory -> Learning -> Radar
```

## Fast Loop: Local Autonomous Trading

This loop should be cheap, deterministic, and always available.

```text
scanner.py
-> candidate discovery
-> market_snapshot.py
-> signals.py
-> decision_pipeline.py
-> agent_decision.py
-> ta_checker.py / risk.py
-> executor.py
-> decision_memory.py
```

Responsibilities:

| Layer | Responsibility |
|---|---|
| Radar | Find market candidates and abnormal conditions |
| Strategy | Turn candidates into long/short hypotheses |
| Risk | Reject bad environments, crowded setups, invalid R/R, and unsafe account states |
| Execution | Open/manage paper trades with deterministic TP/SL/trailing logic |
| Monitoring | Track open positions and close by rules |
| Memory | Record every material open/reject/wait/close decision |

## Slow Loop: Agent Learning

This loop can use Hermes, MAKIMA, OpenClaw, or a future custom agent. It does not
need to run on every market tick.

```text
review_due_decisions()
-> daily reflection report
-> agent reviews failures and missed opportunities
-> store_reflection()
-> self_optimizer.py suggests threshold changes
-> state.json updates
-> next trading cycle uses new lessons / thresholds
```

Good agent jobs:

- Explain why a decision failed
- Identify ignored signals or conflicting context
- Summarize what should change next time
- Suggest conservative parameter changes from reviewed samples
- Review whether a strategy family still has positive expectancy

Bad agent jobs:

- Bypass risk checks
- Trade live without a safety gate
- Rewrite parameters without enough reviewed data
- Run full LLM reasoning for every weak signal

## Current Code Map

| File | Current Role | Future Direction |
|---|---|---|
| `scanner.py` | Main autonomous paper-trading orchestrator | Keep as engine entry |
| `market_snapshot.py` | Futures market context | Keep |
| `signals.py` | Context scoring and tags | Keep |
| `strategies/detectors.py` | Seed strategy signals | Expand carefully |
| `decision_pipeline.py` | Deterministic pre-trade filters | Keep and make thresholds explicit |
| `agent_decision.py` | Local paper-trading decision gate | Rename later to `trade_gate.py` |
| `decision_provider.py` | Old provider/router abstraction | Simplify or remove |
| `executor.py` | Paper execution and position management | Keep, later split paper/live adapters |
| `risk.py` | Account and entry risk checks | Keep |
| `decision_memory.py` | Snapshots, review, experience cases | Keep |
| `agent_tools.py` | External agent review/tool interface | Keep, reposition as learning interface |
| `self_optimizer.py` | Threshold diagnostics | Keep, require enough samples |
| `semantic_radar.py` | Manual semantic event inbox | Expand into real radar input layer |
| `examples/main_agent.py` | Legacy/manual demo | ✅ Moved to examples |
| `examples/agent_framework.py` | Experimental loop demo | ✅ Moved to examples |

## Target Architecture

```text
trading-core/
  radar/
    funding_oi.py
    social_heat.py
    semantic_events.py
    onchain_events.py
  strategies/
    detectors.py
  risk/
    account.py
    entry_quality.py
    trade_setup.py
  execution/
    paper.py
    live_adapter.py      # future, disabled by default
  memory/
    decisions.py
    experiences.py
    reports.py
  learning/
    self_optimizer.py
    agent_review_tools.py
  web/
    app.py
```

This target does not need to be reached in one big move. The first priority is
to simplify the concept and prove profitability in paper trading.

## Web Console

The Web UI should become the main operating console.

Required views:

- Dashboard: equity, open positions, PnL, drawdown, engine status
- Radar: market candidates, signals, snapshots, semantic events
- Decisions: opened/rejected/wait records with reasons and context
- Positions: TP/SL/trailing state and manual controls
- Memory: reviewed decisions, lessons, and similar experiences
- Agent Review: reflection prompts and stored lessons
- Optimizer: threshold diagnostics, apply/rollback controls
- Backtest: run tests, view attribution, inject historical experience
- Settings: risk limits, strategy params, paper/live mode, kill switch

## Safety Boundary

Live trading requires a separate readiness gate:

- Paper sample size target reached
- Positive expectancy after fees and estimated slippage
- Maximum drawdown within limit
- Daily loss limit implemented
- Kill switch implemented
- Order idempotency implemented
- Exchange error handling implemented
- Dry-run reconciliation completed
- Small-capital rollout plan approved
