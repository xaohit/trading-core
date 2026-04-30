# trading-core

Autonomous crypto trading agent with adaptive learning — Binance USD-M Futures, paper trading mode.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your Binance API keys
```

## Run

```bash
python main.py
```

## Architecture

- `main.py` — entry point, mode selection
- `scanner.py` — token scanner, filters by volume/liquidity
- `signals.py` — signal generation from technical indicators
- `executor.py` — order execution
- `risk.py` — risk management
- `memory.py` — adaptive learning (strategy parameter evolution)
- `db/` — SQLite trades database
