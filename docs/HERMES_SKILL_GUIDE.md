# Hermes Skill Guide

`trading-core` should be used as a trading skill/toolkit by Hermes, MAKIMA, OpenClaw, or another agent.

The agent is the caller. The repository is the skill.

```text
Hermes / MAKIMA
-> imports agent_tools.py
-> asks for market context
-> validates setup
-> records decision
-> reviews outcomes
-> writes reflection back into experience memory
```

The system can also run its own paper-trading daemon, but that is fallback automation. The primary design is that an external agent uses these tools.

## Guardrails

- Paper trading and research only by default.
- Do not treat this as a live trading engine.
- Do not bypass `validate_trade_setup()` for trade ideas.
- Record every material `open_long`, `open_short`, and `wait` decision.
- If conviction is unclear, output `wait`.
- If R/R is below 1.5, output `wait`.
- If a failed decision is reviewed, write a lesson with `store_reflection()`.

## First Call

Hermes should start with:

```python
from agent_tools import get_skill_manifest

manifest = get_skill_manifest()
```

This returns the available tools, expected JSON format, and guardrails.

## Normal Decision Workflow

For a symbol under review:

```python
from agent_tools import (
    get_market_analysis,
    validate_trade_setup,
    record_agent_decision,
)

data = get_market_analysis("SOLUSDT")

# Hermes reasons over:
# - data["snapshot"]
# - data["signal_analysis"]
# - data["market_state"]
# - data["active_signals"]
# - data["relevant_experiences"]

validation = validate_trade_setup(
    symbol="SOLUSDT",
    direction="long",
    entry_price=100.0,
    stop_loss=95.0,
)

record_agent_decision(
    symbol="SOLUSDT",
    action="wait",
    direction="long",
    conviction=64,
    reasoning="Wait: similar failed experiences and R/R not clean enough.",
    macro_context=data.get("macro_context"),
    market_state=data.get("market_state"),
)
```

## Expected Hermes JSON

Hermes should reason internally, then produce a compact object:

```json
{
  "action": "open_long",
  "conviction": 72,
  "hypothesis": "Funding is deeply negative while OI remains elevated, suggesting crowded shorts may unwind.",
  "expected_path": "Price should reclaim the nearby liquidity level within 24h without losing the invalidation level first.",
  "stop_loss": 95.0,
  "target_price": 108.0,
  "invalidation_condition": "If price breaks 95.0 or taker flow turns strongly sell-side, the squeeze thesis is wrong.",
  "reasoning": "Current market score is healthy; one similar experience worked, one failed during macro risk. Conviction reduced."
}
```

Then the agent should call `record_agent_decision()` with the final choice.

## Reflection Workflow

Daily or periodically:

```python
from agent_tools import review_due_decisions, store_reflection

result = review_due_decisions()
```

If `reflection_required` is true, Hermes should answer each prompt and store the lesson:

```python
store_reflection(
    decision_id=123,
    reflection_text="Ignored macro risk and over-weighted funding. Next time discount longs when semantic/macro risk is bearish.",
    tags=["funding", "macro_risk", "failed_long"],
    adjustment={"conviction_delta": -8, "requires_extra_confirmation": True}
)
```

## Daily Desk Review

Hermes can request a compact daily summary instead of reading raw database rows:

```python
from agent_tools import get_daily_reflection_report

report = get_daily_reflection_report()
```

Use it to answer:

- Which rejection layer dominates?
- Which signal families reached Agent review?
- Which experiences were reused?
- Did semantic/macro events invalidate local decisions?
- What one rule should be watched tomorrow?

## Semantic Events

External agents can add news, macro, KOL, or Polymarket events:

```python
from agent_tools import add_semantic_event

add_semantic_event(
    symbol="ETHUSDT",
    event_type="polymarket",
    severity=80,
    direction_hint="bearish",
    summary="Prediction market shifted against ETH event outcome.",
    source="polymarket"
)
```

Semantic events do not trade by themselves. They enrich later decisions and can trigger extra human/agent review.

## Research Tools

Use these before trusting a new idea:

```python
from agent_tools import run_backtest, run_monte_carlo_analysis, inject_historical_experience

run_backtest("SOLUSDT", "2025-01-01", "2025-04-01")
run_monte_carlo_analysis("SOLUSDT", "2025-01-01", "2025-04-01")
inject_historical_experience("SOLUSDT", "2025-01-01", "2025-04-01")
```

## What Hermes Should Not Do

- Do not scan every symbol with full LLM reasoning.
- Do not enter live trading credentials or enable live execution.
- Do not skip decision records.
- Do not turn every weak signal into a trade.
- Do not rewrite strategy thresholds without a daily/weekly review reason.

## Mental Model

```text
trading-core = market radar + risk guard + memory + paper execution
Hermes      = human-like trader + reviewer + lesson writer
```

The skill gives Hermes structured context and discipline. Hermes gives the skill judgment and reflection.
