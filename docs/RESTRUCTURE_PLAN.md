# Restructure Plan

This plan repositions Trading Core as a Web-first autonomous trading system with
an agent-assisted learning layer.

Final product shape:

```text
Web Console
-> operate the trading engine
-> observe positions and risk
-> review decisions
-> approve or reject tuning suggestions
-> manage agent reflections and lessons
```

All normal operations should be available from the Web UI. Scripts remain useful
for development and automation, but the product should be operable from the
browser.

## New Product Direction

Old mixed framing:

```text
trading skill vs agent runtime vs autonomous daemon
```

New framing:

```text
Trading Core = autonomous trading system
Agent layer  = review / tuning / learning assistant
Web UI       = main operating console
```

## Phase 1: Clarify Runtime Boundaries

Goal: make the system easy to understand before moving files.

Actions:

- Treat `scanner.py` as the autonomous trading engine entry.
- Treat `web.py` as the main product surface.
- Treat `agent_tools.py` as the agent learning/review interface.
- Treat `main_agent.py` and `agent_framework.py` as legacy demos.
- Remove wording that implies Trading Core must call Hermes API in the live path.
- Keep live trading disabled.

Acceptance:

- README and architecture docs describe one clear product.
- A new contributor can explain the system as:
  "local engine trades; agent reviews; Web controls everything."

## Phase 2: Web Console Completion

Goal: every important operation should be possible from the Web UI.

Required views:

- Dashboard: equity, open positions, PnL, drawdown, engine status
- Radar: candidates, signal reasons, market snapshots, semantic events
- Decisions: opened/rejected/wait decisions with reasons and context
- Positions: TP/SL/trailing state, manual close, close all
- Memory: reviewed decisions, experience cases, similar case lookup
- Agent Review: reflection prompts, agent lessons, store reflection
- Optimizer: threshold diagnostics, suggestions, apply/rollback controls
- Backtest: run backtest, view attribution, inject historical experience
- Settings: risk limits, strategy params, paper/live mode, kill switch

Acceptance:

- No common operation requires editing code.
- Risk-changing actions require explicit confirmation.
- Live mode remains unavailable until safety gate is passed.

## Phase 3: Profit Validation

Goal: stop adding features until the system can prove or disprove edge.

Actions:

- Add paper trading performance report.
- Add strategy attribution by signal type, symbol, market regime, and veto reason.
- Track expectancy after estimated fees and slippage.
- Track maximum drawdown and consecutive losses.
- Track whether performance survives removing the largest winning trade.
- Add minimum sample-size rules before optimizer suggestions can be applied.

Acceptance:

- Web UI can answer:
  - Which strategy makes money?
  - Which strategy loses money?
  - Which market regime works?
  - Which veto protects capital?
  - Is the system profitable without one lucky trade?

## Phase 4: Learning Loop Hardening

Goal: make agent-assisted learning useful without letting it overfit.

Actions:

- Generate structured review packages for failed and missed decisions.
- Let agent reflections write to `experience_cases`.
- Require reviewed sample counts before changing thresholds.
- Store every parameter change with reason, source, and rollback value.
- Add weekly strategy review reports.

Acceptance:

- Agent suggestions are auditable.
- Every tuning change can be explained and rolled back.
- The fast trading loop remains deterministic.

## Phase 5: Module Cleanup

Goal: reduce conceptual noise and align folders with the product model.

Target structure:

```text
radar/
strategies/
risk/
execution/
memory/
learning/
web/
```

Actions:

- Rename `agent_decision.py` to a clearer local gate name.
- Simplify or remove `decision_provider.py` if it only adds confusion.
- Move legacy demos under `examples/`.
- Split paper execution from future live execution adapters.
- Keep compatibility wrappers during migration.

Acceptance:

- Main engine path is obvious.
- Agent review path is separate.
- Web product path is obvious.

## Phase 6: Live Trading Readiness Gate

Goal: only consider live trading after paper proof and safety controls.

Required:

- Paper sample size target reached
- Positive expectancy after fees/slippage
- Max drawdown within configured limit
- Daily loss limit
- Max position count
- Per-trade risk cap
- Consecutive-loss pause
- Manual kill switch
- Order idempotency
- Exchange error handling
- Dry-run reconciliation
- Small-capital rollout checklist

Acceptance:

- The Web UI clearly shows live readiness status.
- Live trading cannot be enabled accidentally.
