"""
Phase 9A: Monte Carlo Simulation.
Assesses the robustness of a strategy by randomizing trade sequences.
"""

import random
from typing import List, Dict

def run_monte_carlo(
    trades: List[Dict],
    num_simulations: int = 1000,
    risk_per_trade_pct: float = 0.02,
    initial_balance: float = 10000.0
) -> Dict:
    """
    Run Monte Carlo simulation based on historical trades.
    
    1. Shuffles trades (simulates different luck/timing).
    2. Compounds equity using Fixed Fractional sizing (Risk %).
    3. Calculates Max Drawdown and Final Equity distribution.
    """
    
    if not trades:
        return {"error": "No trades provided"}

    # We use the pnl_pct which is usually the % return on the specific trade size.
    # But to simulate portfolio impact: Impact = pnl_pct * (Position_Size / Equity).
    # If Position Size is determined by risk_pct / stop_loss%, this gets complex.
    # Simplification: Assume pnl_pct is the % change to the *account* for that trade 
    # (or we scale it by risk_per_trade_pct if pnl_pct is relative to position).
    # Let's assume pnl_pct is raw return on equity (e.g. +0.01 means +1% account).
    pnl_list = [t.get("pnl_pct", 0) / 100.0 for t in trades]
    
    # If pnl_pct is small (like 0.05%), it might be position return.
    # If it is large (like 5.0%), it is account return.
    # Let's check max pnl.
    if max(abs(p) for p in pnl_list) < 0.01: # Likely position return
        # Scale by risk to get account return
        pnl_list = [p * (risk_per_trade_pct / 0.05) for p in pnl_list] # Assume 5% avg SL? No.
        # Safest assumption for Monte Carlo is usually: PnL % = Account % impact.
        pass 
    else:
        pass # Assume account %

    sim_results = []
    
    for _ in range(num_simulations):
        # Bootstrap: Randomly select trades WITH replacement.
        # This simulates taking a new sample of trades from the same strategy distribution.
        shuffled_pnl = random.choices(pnl_list, k=len(pnl_list))
        
        current_balance = initial_balance
        max_balance = initial_balance
        max_drawdown = 0.0
        
        for pnl in shuffled_pnl:
            # Simple compounding: Balance * (1 + PnL%)
            current_balance *= (1 + pnl)
            
            if current_balance > max_balance:
                max_balance = current_balance
            
            drawdown = (max_balance - current_balance) / max_balance
            if drawdown > max_drawdown:
                max_drawdown = drawdown
                
            # Risk of Ruin
            if current_balance < initial_balance * 0.5:
                break
        
        sim_results.append({
            "final_equity": current_balance,
            "max_drawdown": max_drawdown
        })

    # Analyze Results
    final_equities = [r["final_equity"] for r in sim_results]
    max_drawdowns = [r["max_drawdown"] for r in sim_results]
    
    final_equities.sort()
    max_drawdowns.sort()
    
    ruin_count = sum(1 for e in final_equities if e < initial_balance * 0.5)
    
    return {
        "simulations": num_simulations,
        "confidence_intervals": {
            "final_equity_5th": round(final_equities[int(num_simulations * 0.05)], 2),
            "final_equity_50th": round(final_equities[int(num_simulations * 0.50)], 2),
            "final_equity_95th": round(final_equities[int(num_simulations * 0.95)], 2),
            "max_drawdown_5th": round(max_drawdowns[int(num_simulations * 0.05)] * 100, 2),
            "max_drawdown_50th": round(max_drawdowns[int(num_simulations * 0.50)] * 100, 2),
            "max_drawdown_95th": round(max_drawdowns[int(num_simulations * 0.95)] * 100, 2),
        },
        "risk_of_ruin_pct": round((ruin_count / num_simulations) * 100, 2),
        "mean_final_equity": round(sum(final_equities) / len(final_equities), 2),
        "mean_max_drawdown_pct": round((sum(max_drawdowns) / len(max_drawdowns)) * 100, 2),
    }
