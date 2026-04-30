"""
Phase 8B: Experience Injector.
Transforms backtest results into the Agent's Experience Library.
This allows the Agent to start with "historical wisdom" rather than a blank slate.
"""

import sys
import json
from pathlib import Path

# Setup paths
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from db.connection import get_db, init_db
    from decision_memory import DecisionMemory
    from backtest import BacktestEngine, fetch_klines
except ImportError:
    from .db.connection import get_db, init_db
    from .decision_memory import DecisionMemory
    from .backtest import BacktestEngine, fetch_klines

def inject_backtest_results(symbol: str, start: str, end: str, interval: str = "15m"):
    """
    1. Run backtest for the given period.
    2. Parse trades (wins and losses).
    3. Inject significant trades into experience_cases to build the "Memory".
    """
    print(f"🚀 Running backtest for {symbol} ({start} to {end})...")
    
    # Fetch data
    klines = fetch_klines(symbol, interval, start, end)
    if not klines:
        print("❌ Failed to fetch klines. Check proxy or date range.")
        return

    # Run engine
    engine = BacktestEngine(
        symbols=[symbol],
        klines_by_symbol={symbol: klines},
        sizing_mode="atr",
        risk_pct=2.0,
        leverage=3,
        min_score=50, # Relaxed score for backtest to generate more samples
        tp1_r=1.5,
        tp2_r=3.0
    )
    result = engine.run()
    
    trades = result.get("trades", [])
    if not trades:
        print("⚠️ No trades generated in backtest period. No experience injected.")
        return

    print(f"📊 Found {len(trades)} trades. Analyzing for experience injection...")
    
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    
    injected_count = 0

    for trade in trades:
        pnl = trade.get("pnl_pct", 0)
        signal_type = trade.get("signal_type", "unknown")
        direction = trade.get("direction", "unknown")
        
        # Heuristic: Only learn from significant moves or clear losses.
        # We filter out tiny noise trades to keep the experience library high-quality.
        if abs(pnl) < 1.0:
            continue 

        # Determine outcome label and Lesson text
        if pnl > 0:
            outcome_label = "target_hit" if pnl > 3.0 else "direction_correct"
            lesson = f"Successful {signal_type} on {symbol} ({direction}). PnL: {pnl:.2f}%. Signal logic held up."
            tags = ["backtest", "success", signal_type]
        else:
            outcome_label = "direction_wrong"
            lesson = f"Failed {signal_type} on {symbol} ({direction}). Loss: {pnl:.2f}%. Signal was likely a trap or premature."
            tags = ["backtest", "failure", signal_type]
            # Add specific tags for better retrieval later
            if "crash" in signal_type: tags.append("crash_trap")
            if "pump" in signal_type: tags.append("pump_trap")
            
        # Archive experience
        # We simulate a minimal context for historical injection
        context = {
            "signal": {"type": signal_type, "direction": direction},
            "analysis": {"score": 0, "tags": tags},
            "snapshot": {}
        }
        
        # We need a mock decision dict for _archive_experience
        decision = {
            "id": None, # Virtual ID
            "symbol": symbol,
            "signal_type": signal_type,
            "direction": direction
        }

        DecisionMemory._archive_experience(
            cursor,
            decision,
            context,
            {"outcome_label": outcome_label, "return_pct": pnl},
            tags
        )
        injected_count += 1

    conn.commit()
    print(f"✅ Successfully injected {injected_count} historical experiences into memory.")

if __name__ == "__main__":
    # Example usage: Inject BTC history from last 3 months
    inject_backtest_results("BTCUSDT", "2025-01-01", "2025-04-01")
