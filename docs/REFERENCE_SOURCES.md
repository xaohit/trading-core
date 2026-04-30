# Reference Sources

Last checked: 2026-04-30

## Sources

1. `https://connectfarm1.com/`
   - Role: seed strategy prototype and product direction.
   - Relevant snippet: `Futures + Alpha Autonomous Trading v1`.
   - Core idea: autonomous loop of scan -> analyze -> virtual open -> monitor -> close -> review.
   - Strategy seeds: negative funding long, positive funding short, pump short, crash bounce long.
   - Stated status: paper trading only; treat as prototype, not live-trading proof.

2. `https://github.com/ZAIJIN88/binance-square-monitor`
   - Role: mature architecture reference for market data, social heat, scoring, risk, paper trading, and dashboard.
   - Local reference copy: `reference_sources/binance-square-monitor/binance-square-monitor/` (ignored by git).
   - Key files:
     - `scraper.py`: Binance Square social heat capture via Playwright and intercepted JSON responses.
     - `analyzer.py`: token extraction, author filtering, heat score, composite scoring.
     - `market.py` / `market_realtime.py`: Binance futures snapshot and realtime market metrics.
     - `signals.py`: 0-100 scoring and verdict synthesis.
     - `risk.py`: ATR stop distance, risk-based sizing, account circuit breakers, sector concentration.
     - `trade_logic.py`: paper entries, TP pyramid, trailing stop, failure tags.
     - `storage.py`: SQLite persistence model.
     - `web.py`: operational dashboard.

## Mapping To This Project

Already present in `trading-core`:
- Paper-trading-only architecture.
- Binance USD-M futures public market data.
- Seed strategies from `connectfarm1.com`.
- `market_snapshot.py`: OI, LSR, taker flow, depth, ATR, multi-timeframe changes.
- `signals.py`: score, verdict, tags, notes.
- Basic dashboard, state, trades DB, manual close, scan.

Still to port or finish:
- Phase 3: integrate `market_snapshot.get_market_snapshot()` and `signals.analyze()` into `scanner.py` and Web output.
- Phase 4: port ATR risk-based sizing and TP pyramid from `risk.py` / `trade_logic.py`.
- Phase 5: port social heat candidate pool from `scraper.py` / `analyzer.py`.
- Add failure archive tags for stopped-out trades.
- Expand tests around risk, entry quality, TP stages, and scanner integration.

## Implementation Guardrails

- Keep live trading disabled unless explicitly added behind a hard opt-in switch.
- Prefer importing ideas and behavior, not wholesale copying large files.
- Preserve the current small-module layout of `trading-core`.
- Every phase should update `docs/PROJECT_STATE.md` with files touched, commands run, and verification result.
