# Trading Core Project Plan

Last updated: 2026-04-30

## Product Goal

Build a paper-first autonomous crypto futures trading system that can:

1. Discover tradable symbols from Binance futures market data and, later, Binance Square social heat.
2. Build a multi-dimensional market snapshot for each candidate.
3. Score each candidate with a transparent 0-100 rule engine.
4. Open paper positions only when strategy, market quality, and account risk all agree.
5. Monitor positions, close them by risk rules, and archive why wins/losses happened.
6. Show the whole loop in a local Web dashboard.

This system is not ready for live trading. Live execution must stay disabled until paper results, risk controls, tests, and operational controls are mature.

## Reference Roles

- `connectfarm1.com`: seed prototype and strategy intent.
  It defines the autonomous loop and the initial four futures strategies.
- `ZAIJIN88/binance-square-monitor`: engineering reference.
  It provides mature ideas for social heat, market snapshot, scoring, ATR risk, TP pyramid, paper trading, failure tags, and dashboard structure.

## Current System

Implemented:

- Python service with `main.py`, `server.py`, `web.py`.
- Paper trading only.
- Binance USD-M futures public data.
- Four seed strategy detectors:
  - `neg_funding_long`
  - `pos_funding_short`
  - `crash_bounce_long`
  - `pump_short`
- SQLite persistence for trades, signal history, K-lines, daily stats, and strategy evolution.
- Basic account state, cooldown, daily loss and drawdown checks.
- Web dashboard for balance, equity, open positions, closed trades, manual close, and manual scan.
- `market_snapshot.py`: OI, funding, LSR, taker flow, depth, ATR, multi-timeframe price changes.
- `signals.py`: 0-100 score, verdict, tags, notes, OI divergence.
- Smoke tests for market snapshots and scoring.

Known gaps:

- New snapshot/scoring layer is not fully integrated into scanner decisions.
- Risk sizing is still position-percent based instead of risk-per-trade based.
- Stops and take profits are fixed per strategy, not ATR-adaptive.
- No TP1/TP2/trailing-stop pyramid.
- No social heat candidate pool yet.
- Failure archive tags are not persisted.
- Test coverage is thin.

## Target Architecture

### 1. Candidate Discovery

Inputs:
- Binance futures tickers and funding rates.
- Later: Binance Square 15-minute social heat leaderboard.

Output:
- Ranked symbols with a candidate reason: strategy signal, social heat, volume/OI anomaly, or watchlist.

### 2. Market Snapshot

For each candidate, collect:
- price
- 15m / 1h / 4h / 24h change
- open interest and OI changes
- funding rate
- global and top-trader long/short ratios
- taker buy/sell ratio and trend
- depth imbalance inside +/-1%
- ATR percentage
- 24h quote volume

### 3. Signal Scoring

The scoring engine returns:
- `score`: 0-100
- `verdict`: healthy / watch / overheated / weak / neutral / insufficient
- `tags`: machine-readable labels
- `notes`: human-readable reasons
- `oi_divergence`: divergence warning or watch signal

The scanner should persist these fields for every meaningful reject/open decision.

### 4. Entry Decision

Entry requires all of:
- A strategy signal exists.
- Market snapshot is usable.
- Composite score is above the configured threshold.
- Verdict is not overheated or insufficient.
- Account risk allows opening.
- Symbol is not already open and not cooling down.
- Signal strength is not `B`.

The final ranking should combine:
- strategy strength
- composite market score
- environment/risk score
- liquidity and OI quality
- later: social heat rank

### 5. Position Sizing And Risk

Target model:
- Determine stop first.
- Risk per trade = equity * configured risk percentage.
- Position size = risk amount / stop distance.
- Clamp by available balance, max notional, max leverage, and min notional.

Account-level guards:
- daily loss circuit breaker
- max drawdown
- max open positions
- per-symbol cooldown after stop loss
- sector concentration limit
- daily trade count limit

### 6. Exit Management

Target TP pyramid:
- TP1: close 30% at 1.5R and move stop to breakeven.
- TP2: close 30% at 3R.
- Final 40%: trailing stop.
- Hard stop: ATR-adaptive stop.

All exits must be paper-only until live mode is explicitly implemented.

### 7. Learning And Review

Every closed trade should record:
- entry snapshot
- entry score and tags
- exit reason
- PnL
- holding time
- failure tags when stopped out

Failure tags should include:
- hot funding at entry
- crowded LSR at entry
- taker pressure faded
- entry verdict not healthy
- OI reversed
- pure price stop

Strategy evolution should only update parameters after enough samples.

### 8. Dashboard

The dashboard should show:
- account summary
- open positions
- closed history
- scanner status
- latest signal history
- score/verdict/tags per signal
- evolved strategy parameters
- later: social heat leaderboard
- later: failure archive and tag statistics

## Implementation Roadmap

### Phase 0: Runtime Guardrails

Status: mostly done.

Deliverables:
- `requirements.txt`
- Windows-compatible process handling
- UTF-8 output handling
- reference-source documentation

Acceptance:
- Web UI starts locally.
- Smoke tests run.

### Phase 1: Market Snapshot Layer

Status: done.

Acceptance:
- `tests/smoke_market_snapshot.py` prints `PHASE1_SMOKE_OK`.

### Phase 2: Scoring Layer

Status: done.

Acceptance:
- `tests/smoke_signals.py` prints `PHASE2_SMOKE_OK`.

### Phase 3: Scanner Integration

Status: in progress.

Deliverables:
- Scanner fetches snapshot and scoring for every strategy candidate.
- Signal history stores score, verdict, tags, notes, and snapshot payload.
- Opened trades include the scoring analysis in `pre_analysis`.
- Web/API exposes recent scored signals.

Acceptance:
- Existing smoke tests pass.
- `python main.py --once` can run without schema errors.
- `/api/signals` includes new score metadata.

### Phase 4A: Decision Memory Loop

Status: first slice done.

Deliverables:
- `decision_snapshots`: structured decision journal.
- `decision_outcomes`: horizon review results.
- `experience_cases`: compact lessons for future retrieval.
- Scanner writes key decisions: `opened`, `score_reject`, `risk_reject`, `env_reject`.
- Review engine checks price after the configured horizon and records direction correctness, target hit, invalidation, max favorable excursion, and max adverse excursion.
- Web API exposes `/api/decision-memory` and `/api/decision-memory/review-due`.
- Experience retrieval ranks similar cases by same symbol, same strategy, tag overlap, failed-case priority, and recency.
- Scanner injects retrieved Top 3 cases into each new decision context.

Acceptance:
- `tests/smoke_decision_memory.py` prints `DECISION_MEMORY_SMOKE_OK`.
- Decision memory does not place trades or mutate strategy rules.
- Experience cases are archived only as reference material until enough samples exist.
- Retrieval API returns similar cases through `/api/decision-memory/retrieve`.

### Phase 4B: ATR Risk And TP Pyramid

Deliverables:
- ATR-based stop distance.
- Risk-based position sizing.
- TP1/TP2/trailing fields in DB.
- Partial close support for paper positions.
- Breakeven stop after TP1.

Acceptance:
- Unit tests cover long/short TP and stop paths.
- Paper positions update deterministically with mocked prices.

### Phase 5: Social Heat Candidate Pool

Deliverables:
- Binance Square scraper or API-intercept collector.
- Author/token filters.
- 15-minute heat leaderboard.
- Candidate feed into scanner before all-market fallback.

Acceptance:
- Collector can produce a leaderboard.
- Scanner can use social heat without requiring it.

### Phase 6: Reflection And Adaptive Rules

Deliverables:
- Failure tags persisted per stopped-out trade.
- Dashboard section for loss reasons.
- Strategy evolution reads tags as well as PnL.
- Hermes/Claude reflection prompt uses decision snapshots + outcomes.
- Similar experience retrieval injects top historical lessons before analysis.
- Repeated lessons become candidate adaptive rules only after enough samples.

Acceptance:
- Stopped-out paper trade produces tags.
- Dashboard shows tag counts.

### Phase 7: Reliability And Operations

Deliverables:
- Full test suite.
- Process health checks.
- Safer config loading.
- Backup/export of SQLite and state.
- Log rotation.

Acceptance:
- Tests pass from a clean checkout.
- Start/stop/status work on Windows.

### Phase 8: Live-Trading Readiness Review

Deliverables:
- Explicit live mode design.
- Exchange key permission audit.
- Dry-run/live separation.
- Kill switch.
- Minimum paper sample requirements.

Acceptance:
- Live mode remains off by default.
- No live order can be placed without explicit config, confirmation, and tests.

## Immediate Next Step

Continue with Phase 4A retrieval, then Phase 4B ATR risk. The system should remember and review decisions before it is allowed to become more aggressive.
