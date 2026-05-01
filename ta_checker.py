"""
Phase 9B: Technical Analysis & Risk/Reward Checker.

Ensures every trade has a valid technical setup and a favorable Risk/Reward ratio.
No blind entries.
"""
from typing import Optional, Dict, List

def assess_trade_setup(
    symbol: str, 
    direction: str, 
    entry_price: float, 
    stop_loss: float,
    klines: List[Dict]
) -> Dict:
    """
    Analyzes the trade setup based on recent price action.
    Returns:
    - "r_r_ratio": Estimated Risk/Reward ratio.
    - "is_valid": Boolean, whether the setup meets minimum requirements.
    - "reason": Explanation for rejection or approval.
    - "target_price": Calculated realistic take-profit level.
    """
    if not klines or len(klines) < 20:
        return {"is_valid": False, "reason": "Insufficient data", "r_r_ratio": 0}

    # 1. Calculate Risk Distance (Stop Loss distance)
    risk_distance = abs(entry_price - stop_loss)
    if risk_distance == 0:
        return {"is_valid": False, "reason": "Invalid Stop Loss distance", "r_r_ratio": 0}

    # 2. Identify Key Levels (Support/Resistance)
    # We use the highest/lowest prices of the last 50 candles as proxies for liquidity pools
    highs = [float(k['high']) for k in klines[-50:]]
    lows = [float(k['low']) for k in klines[-50:]]
    
    recent_high = max(highs)
    recent_low = min(lows)

    target_price = None

    if direction == "long":
        # Target: Recent high or previous resistance
        potential_reward = recent_high - entry_price
        
        # Validation: Is the target ABOVE the entry by a safe margin?
        if potential_reward <= risk_distance:
            # If the nearest high is too close, maybe look for an extension?
            # But strictly, if immediate resistance is too close, it's a bad R/R.
            r_r = potential_reward / risk_distance
        else:
            r_r = potential_reward / risk_distance
            target_price = recent_high

    else: # short
        # Target: Recent low or previous support
        potential_reward = entry_price - recent_low
        r_r = potential_reward / risk_distance
        
        if r_r > 0:
            target_price = recent_low

    # 3. Minimum Thresholds
    # Professional standard: Risk/Reward must be at least 1.5:1 to justify the trade
    MIN_R_R = 1.5
    
    is_valid = (r_r >= MIN_R_R)

    reason = ""
    if not is_valid:
        reason = f"R/R Ratio {r_r:.2f} is too low (Min {MIN_R_R}). Resistance/Support is too close."
    else:
        reason = f"Setup valid. Potential R/R is {r_r:.2f}. Target near {target_price}."

    return {
        "is_valid": is_valid,
        "r_r_ratio": round(r_r, 2),
        "target_price": round(target_price, 8) if target_price else None,
        "risk_price": stop_loss,
        "reason": reason
    }
