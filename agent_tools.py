"""
Agent Tools Interface.
Exposes core system capabilities as tools for external agents (e.g. OpenClaw, Hermes).
95% of these are local rule execution (0 tokens); LLM is only needed for deep reflection.
"""
import json
import os

try:
    from .market_snapshot import get_market_snapshot
    from .signals import analyze
    from .decision_memory import DecisionMemory
    from .market_state import classify_market_state
    from .market import Market
    from .strategies.detectors import detect_all
    from .config import DECISION_REVIEW_HORIZON_HOURS
except ImportError:
    from market_snapshot import get_market_snapshot
    from signals import analyze
    from decision_memory import DecisionMemory
    from market_state import classify_market_state
    from market import Market
    from strategies.detectors import detect_all
    from config import DECISION_REVIEW_HORIZON_HOURS


def get_market_analysis(symbol: str, macro_data: dict | None = None) -> dict:
    """
    Tool: Get deep market analysis for a symbol.
    Returns snapshot, signals, market state, and past experiences.
    """
    snapshot = get_market_snapshot(symbol)
    if "error" in snapshot:
        return {"error": snapshot["error"]}

    signal_analysis = analyze(snapshot)
    market_state = classify_market_state(symbol)
    tickers = Market.all_tickers()
    ticker = next((t for t in tickers if t["symbol"] == symbol), {})
    funding = Market.funding_rates()
    signals = detect_all(symbol, ticker, funding)
    
    # Contextual experience retrieval (Phase 8D)
    experiences = DecisionMemory.retrieve_for_signal(
        symbol, 
        {"type": signals[0]["type"] if signals else "general", "direction": signals[0]["direction"] if signals else "unknown"}, 
        signal_analysis
    )

    return {
        "symbol": symbol,
        "snapshot": snapshot,
        "signal_analysis": signal_analysis,
        "market_state": market_state,
        "active_signals": signals,
        "relevant_experiences": experiences,
        "macro_context": macro_data or {},
    }


def record_agent_decision(
    symbol: str, 
    action: str,  # "open_long", "open_short", "reject", "wait"
    direction: str | None = None,
    target_price: float | None = None,
    conviction: float = 50.0,
    reasoning: str = "",
    macro_context: dict | None = None,
    market_state: dict | None = None,
    agent_reasoning: str | None = None
) -> dict:
    """
    Tool: Record a decision made by the Agent.
    This creates a snapshot that will be reviewed in 24h.
    """
    signal = {
        "type": "agent_manual",
        "direction": direction,
        "strength": "S" if conviction > 80 else "A",
        "price": get_market_snapshot(symbol).get("price"),
        "sl_pct": 0.05,
        "tp_pct": 0.10,
    }
    
    snapshot = get_market_snapshot(symbol)
    analysis = {"score": conviction, "verdict": "agent_manual", "tags": ["agent_decision"]}
    
    decision_id = DecisionMemory.record_decision(
        symbol=symbol,
        action=action,
        signal=signal,
        snapshot=snapshot,
        analysis=analysis,
        horizon_hours=DECISION_REVIEW_HORIZON_HOURS,
        macro_context=macro_context,
        market_state=market_state,
        agent_reasoning=agent_reasoning
    )
    
    return {"decision_id": decision_id, "status": "recorded"}


def review_due_decisions() -> list[dict]:
    """
    Tool: Trigger 24h review for pending decisions.
    Returns the list of reviewed outcomes.
    """
    reviewed = DecisionMemory.review_due(limit=50)
    
    # Trigger LLM reflection if there were failures
    failures = [r for r in reviewed if r.get("outcome_label") in {"invalidated", "direction_wrong"}]
    if failures:
        return {
            "reviewed": len(reviewed),
            "failures": failures,
            "reflection_required": True,
            "reflection_prompts": [DecisionMemory.reflection_prompt(f["decision_id"]) for f in failures]
        }
    return {"reviewed": len(reviewed), "reflection_required": False}


def get_experience_library(symbol: str = None, limit: int = 10) -> list[dict]:
    """
    Tool: Query the agent's experience library.
    """
    return DecisionMemory.recent_experiences(limit=limit)


def run_backtest(symbol: str, start: str, end: str, sizing: str = "atr") -> dict:
    """
    Tool: Run a backtest and (future) inject results into experience library.
    """
    from .backtest import fetch_klines, BacktestEngine
    klines = fetch_klines(symbol, "15m", start, end)
    if not klines:
        return {"error": "No data"}
    
    engine = BacktestEngine(
        symbols=[symbol],
        klines_by_symbol={symbol: klines},
        sizing_mode=sizing
    )
    result = engine.run()
    return result


def adjust_strategy_params(symbol: str, signal_type: str, new_params: dict, reason: str = "") -> dict:
    """
    Tool: Safely evolve strategy parameters without touching the code.
    Updates state.json which overrides config.py defaults at runtime.
    
    Args:
        symbol: Target symbol (or "GLOBAL" for all).
        signal_type: Strategy name (e.g., "neg_funding_long").
        new_params: Dict of params to change, e.g., {"min_rate": -0.05, "sl_pct": 0.06}.
        reason: Why this change is being made (for logging).
    """
    import json
    from pathlib import Path
    from config import STATE_PATH

    # Ensure the state file exists
    if not STATE_PATH.exists():
        STATE_PATH.write_text("{}")
    
    with open(STATE_PATH, "r") as f:
        try:
            state = json.load(f)
        except Exception:
            state = {}

    if "evolved_params" not in state:
        state["evolved_params"] = {}

    # Update params
    if signal_type not in state["evolved_params"]:
        state["evolved_params"][signal_type] = {}
    
    state["evolved_params"][signal_type].update(new_params)
    state["evolved_params"][signal_type]["_last_updated_reason"] = reason
    state["evolved_params"][signal_type]["_last_updated_time"] = int(time.time())

    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)

    return {"status": "success", "message": f"Updated {signal_type} with {new_params}"}


def inject_historical_experience(symbol: str, start: str, end: str) -> dict:
    """
    Tool: Run a backtest and automatically inject the results as "Experience".
    Use this to give the Agent historical wisdom before live trading.
    """
    try:
        from .experience_injector import inject_backtest_results
        # We capture print output or just run it
        # Since it prints to console, we can return a success message
        inject_backtest_results(symbol, start, end)
        return {"status": "Injection complete", "message": "Check console for details."}
    except Exception as e:
        return {"error": str(e)}
