"""
Agent Tools Interface.
Exposes core system capabilities as tools for external agents (e.g. OpenClaw, Hermes).
95% of these are local rule execution (0 tokens); LLM is only needed for deep reflection.
"""
import json
import os
import time

SKILL_NAME = "trading-core"
SKILL_VERSION = "0.2.0"

try:
    from .market_snapshot import get_market_snapshot
    from .signals import analyze
    from .decision_memory import DecisionMemory
    from .market_state import classify_market_state
    from .market import Market
    from .strategies.detectors import detect_all
    from .config import DECISION_REVIEW_HORIZON_HOURS, STATE_PATH
    from .ta_checker import assess_trade_setup
    from .semantic_radar import SemanticRadar
    from .daily_reflection import build_daily_reflection_report
except ImportError:
    from market_snapshot import get_market_snapshot
    from signals import analyze
    from decision_memory import DecisionMemory
    from market_state import classify_market_state
    from market import Market
    from strategies.detectors import detect_all
    from config import DECISION_REVIEW_HORIZON_HOURS, STATE_PATH
    from ta_checker import assess_trade_setup
    from semantic_radar import SemanticRadar
    from daily_reflection import build_daily_reflection_report


def get_skill_manifest() -> dict:
    """
    Tool: Describe this repository as a Hermes/OpenClaw trading skill.
    Agents should call this first to understand available tools and guardrails.
    """
    return {
        "name": SKILL_NAME,
        "version": SKILL_VERSION,
        "role": "paper-first trading skill for market analysis, decision memory, validation, and reflection",
        "operating_mode": "external_agent_calls_tools",
        "live_trading": "disabled/not recommended",
        "primary_agent_workflow": [
            "get_skill_manifest",
            "get_market_analysis",
            "validate_trade_setup",
            "record_agent_decision",
            "review_due_decisions",
            "store_reflection",
            "get_daily_reflection_report",
        ],
        "tool_groups": {
            "market_analysis": [
                "get_market_analysis",
                "get_experience_library",
                "add_semantic_event",
            ],
            "decision_memory": [
                "record_agent_decision",
                "review_due_decisions",
                "store_reflection",
                "get_daily_reflection_report",
            ],
            "risk_validation": [
                "validate_trade_setup",
            ],
            "research": [
                "run_backtest",
                "run_monte_carlo_analysis",
                "inject_historical_experience",
            ],
            "evolution": [
                "adjust_strategy_params",
            ],
        },
        "guardrails": [
            "paper trading only unless the human explicitly performs a separate live-trading safety review",
            "record every material trade/wait decision",
            "do not bypass local TA/RR, risk, and memory checks",
            "default to wait when conviction is low or risk/reward is invalid",
            "write failed-decision lessons back with store_reflection",
        ],
        "expected_decision_json": {
            "action": "open_long | open_short | wait | close",
            "conviction": "0-100",
            "hypothesis": "why this setup may work",
            "expected_path": "what price/market behavior should happen",
            "stop_loss": "numeric price or null",
            "target_price": "numeric price or null",
            "invalidation_condition": "what proves the idea wrong",
            "reasoning": "compact explanation using current context and past experiences",
        },
    }


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
    stop_loss: float | None = None,
    target_price: float | None = None,
    conviction: float = 50.0,
    reasoning: str = "",
    reason: str | None = None,
    hypothesis: str | None = None,
    expected_path: str | None = None,
    invalidation_condition: str | None = None,
    macro_context: dict | None = None,
    market_state: dict | None = None,
    agent_reasoning: str | None = None
) -> dict:
    """
    Tool: Record a decision made by the Agent.
    This creates a snapshot that will be reviewed in 24h.
    """
    snapshot = get_market_snapshot(symbol)
    price = snapshot.get("price")
    if direction is None:
        if action == "open_long":
            direction = "long"
        elif action == "open_short":
            direction = "short"

    sl_pct = _pct_distance(price, stop_loss, direction)
    tp_pct = _pct_distance(price, target_price, direction)
    signal = {
        "type": "agent_manual",
        "direction": direction,
        "strength": "S" if conviction > 80 else "A",
        "price": price,
        "stop_loss": stop_loss,
        "target_price": target_price,
        "sl_pct": sl_pct if sl_pct is not None else 0.05,
        "tp_pct": tp_pct if tp_pct is not None else 0.10,
        "hypothesis": hypothesis,
        "expected_path": expected_path,
        "invalidation_condition": invalidation_condition,
    }
    
    analysis = {
        "score": conviction,
        "verdict": "agent_manual",
        "tags": ["agent_decision", f"action:{action}"],
        "notes": [item for item in [hypothesis, expected_path, invalidation_condition] if item],
    }
    final_reasoning = reasoning or reason or ""
    
    decision_id = DecisionMemory.record_decision(
        symbol=symbol,
        action=action,
        signal=signal,
        snapshot=snapshot,
        analysis=analysis,
        result=final_reasoning,
        horizon_hours=DECISION_REVIEW_HORIZON_HOURS,
        macro_context=macro_context,
        market_state=market_state,
        agent_reasoning=agent_reasoning or final_reasoning
    )
    
    return {"decision_id": decision_id, "status": "recorded"}


def _pct_distance(entry_price, level_price, direction: str | None) -> float | None:
    try:
        entry = float(entry_price)
        level = float(level_price)
    except (TypeError, ValueError):
        return None
    if entry <= 0 or level <= 0 or direction not in {"long", "short"}:
        return None
    if direction == "long":
        return abs(level - entry) / entry
    return abs(entry - level) / entry


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


def get_daily_reflection_report(limit: int = 80) -> dict:
    """
    Tool: Build a compact daily trading desk review for Hermes.
    """
    return build_daily_reflection_report(limit=limit)


def add_semantic_event(
    symbol: str,
    event_type: str,
    severity: int,
    direction_hint: str,
    summary: str,
    source: str = "manual",
) -> dict:
    """
    Tool: Add a news/macro/KOL/Polymarket style event to the semantic radar.
    Events do not trade directly; they can trigger Hermes review.
    """
    return SemanticRadar.add_event(
        symbol=symbol,
        event_type=event_type,
        severity=severity,
        direction_hint=direction_hint,
        summary=summary,
        source=source,
    )


def run_backtest(symbol: str, start: str, end: str, sizing: str = "atr") -> dict:
    """
    Tool: Run a backtest and (future) inject results into experience library.
    """
    try:
        from .backtest import fetch_klines, BacktestEngine
    except ImportError:
        from backtest import fetch_klines, BacktestEngine

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


def run_monte_carlo_analysis(
    symbol: str = None,
    start: str = "2025-01-01",
    end: str = "2025-04-01",
    num_simulations: int = 1000
) -> dict:
    """
    Tool: Run Backtest and then perform Monte Carlo Simulation.
    Returns probability distributions of outcomes to check strategy robustness.
    """
    # 1. Run Backtest
    backtest_res = run_backtest(symbol, start, end)
    if "error" in backtest_res or "trades" not in backtest_res:
        return {"error": "Backtest failed or no trades"}
        
    trades = backtest_res.get("trades", [])
    if len(trades) < 10:
        return {"error": "Not enough trades for meaningful simulation"}
        
    # 2. Run Monte Carlo
    try:
        try:
            from .monte_carlo import run_monte_carlo
        except ImportError:
            from monte_carlo import run_monte_carlo

        mc_result = run_monte_carlo(
            trades=trades,
            num_simulations=num_simulations,
            risk_per_trade_pct=0.02, # Default risk
            initial_balance=10000.0
        )
        return {
            "status": "success",
            "backtest_summary": {
                "trades_count": len(trades),
                "win_rate": backtest_res.get("win_rate")
            },
            "monte_carlo": mc_result
        }
    except Exception as e:
        return {"error": f"Monte Carlo failed: {str(e)}"}


def validate_trade_setup(
    symbol: str,
    direction: str,
    entry_price: float,
    stop_loss: float
) -> dict:
    """
    Tool: Technical Validation Check.
    Analyzes recent market structure to ensure the trade has a valid setup and Risk/Reward > 1.5.
    
    The Agent MUST call this before opening any trade.
    """
    klines = Market.klines(symbol, "1h", limit=50)
    if not klines:
        return {"error": "Failed to fetch klines for analysis"}
        
    return assess_trade_setup(symbol, direction, entry_price, stop_loss, klines)

def inject_historical_experience(symbol: str, start: str, end: str) -> dict:
    """
    Tool: Run a backtest and automatically inject the results as "Experience".
    Use this to give the Agent historical wisdom before live trading.
    """
    try:
        try:
            from .experience_injector import inject_backtest_results
        except ImportError:
            from experience_injector import inject_backtest_results

        # We capture print output or just run it
        # Since it prints to console, we can return a success message
        inject_backtest_results(symbol, start, end)
        return {"status": "Injection complete", "message": "Check console for details."}
    except Exception as e:
        return {"error": str(e)}


def store_reflection(
    decision_id: int,
    reflection_text: str,
    tags: list[str] | None = None,
    adjustment: dict | None = None,
) -> dict:
    """
    Tool: Persist an LLM reflection as a reusable experience case.
    This is the bridge from review failures into future context retrieval.
    """
    try:
        try:
            from .db.connection import get_db, init_db
        except ImportError:
            from db.connection import get_db, init_db

        init_db()
        decision = DecisionMemory.get_decision(decision_id)
        if not decision:
            return {"status": "error", "error": f"decision {decision_id} not found"}

        tag_list = tags or ["agent_reflection"]
        lesson = reflection_text.strip()
        searchable = " | ".join(
            str(part)
            for part in [
                decision.get("symbol"),
                decision.get("signal_type"),
                decision.get("direction"),
                decision.get("reasoning"),
                lesson,
                " ".join(tag_list),
            ]
            if part
        )

        conn = get_db()
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO experience_cases
            (source_snapshot_id, symbol, signal_type, outcome_label, tags,
             lesson, adjustment_json, searchable_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision_id,
                decision.get("symbol"),
                decision.get("signal_type"),
                "llm_reflection",
                json.dumps(tag_list, ensure_ascii=False),
                lesson,
                json.dumps(adjustment or {}, ensure_ascii=False),
                searchable,
            ),
        )
        conn.commit()
        return {"status": "stored", "experience_id": c.lastrowid}
    except Exception as e:
        return {"status": "error", "error": str(e)}
