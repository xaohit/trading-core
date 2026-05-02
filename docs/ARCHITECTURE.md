# Trading Core Architecture

This project is a paper-first trading skill for Hermes, MAKIMA, OpenClaw, or
another external agent. The agent is the trader. `trading-core` is the toolkit:
market radar, deterministic guards, decision memory, reflection storage,
backtesting, and paper execution.

## Primary Path: Skill-First

Hermes should use this repository by importing `agent_tools.py`.

```text
Hermes / MAKIMA
-> agent_tools.get_skill_manifest()
-> agent_tools.get_market_analysis(symbol)
-> Hermes produces a structured trade/wait hypothesis
-> agent_tools.validate_trade_setup(...)
-> agent_tools.record_agent_decision(...)
-> agent_tools.review_due_decisions()
-> agent_tools.store_reflection(...)
```

This is the main architecture. It keeps the model in the role of a human trader:
reading context, forming hypotheses, accepting/rejecting trades, and writing
lessons after review.

## Local Daemon Path: Fallback

The daemon is useful for paper-trading observation and low-token operation.

```text
scanner.py
-> strategy candidate discovery
-> DecisionPipeline
-> ranking
-> DecisionProvider
-> TA/RR guard
-> paper execution
-> DecisionMemory
```

`DecisionProvider` routes ordinary cases to the deterministic local
`AgentDecisionGate`. The Hermes provider is intentionally safe until a real
client is wired: it returns `wait`.

## Legacy / Experimental Path

`main_agent.py` and `agent_framework.py` are not the primary integration path.
They remain as experimental/manual demos for agent loops and reflection routines.
New agent integrations should prefer `agent_tools.py`.

## Core Boundaries

| Layer | File | Responsibility |
|---|---|---|
| Skill interface | `agent_tools.py` | Stable external-agent API |
| Candidate discovery | `strategies/detectors.py` | Seed strategy signals only |
| Market context | `market_snapshot.py`, `signals.py` | Snapshot, scoring, tags |
| Pre-agent guard | `decision_pipeline.py` | Deterministic vetoes and risk quality |
| Fallback decision | `agent_decision.py` | Local trade/wait judgment |
| Provider router | `decision_provider.py` | Local vs Hermes routing for daemon mode |
| Trade validation | `ta_checker.py`, `risk.py` | Hard R/R and account guards |
| Execution | `executor.py` | Paper execution and position management |
| Memory loop | `decision_memory.py` | Snapshots, 24h review, experience cases |
| Optimizer | `self_optimizer.py` | Threshold diagnostics and state updates |

## Safety Rules

- Live trading is out of scope until a separate safety review is done.
- Every material trade or wait decision should be recorded.
- Hermes can reason, but local hard guards must remain deterministic.
- Optimizer changes must go through `state.json`; code defaults stay conservative.
- Failed reviewed decisions should produce lessons through `store_reflection()`.

## Current High-Value Next Work

1. Keep enriching decision snapshots with macro, order book, Polymarket, and
   explicit hypothesis fields.
2. Accumulate reviewed paper decisions before trusting optimizer suggestions.
3. Wire a real Hermes client only if the project intentionally moves beyond
   skill-first usage.
4. Add a full pytest suite once the smoke-test surface stabilizes.
