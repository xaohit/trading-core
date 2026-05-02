## Issue #1: taker_trend ≤ -5% 逻辑用反了

**Severity:** High  
**File:** `decision_pipeline.py` L148-150

### 问题描述

做空信号（`pos_funding_short`）被 `taker_trend=-39%` 拒绝，但 taker 主动卖出强（负值大）恰好是做空策略的**正面信号**，不是负面。

代码：
```python
taker_trend = snapshot.get("taker_trend_pct", 0) or 0
if taker_trend <= -5:
    return f"taker trend={taker_trend}% <= -5%"
```

### 根因

`taker_trend_pct` 负值代表主动卖出，正值代表主动买入。做空策略（short）希望看到强主动卖出，`taker_trend <= -5%` 恰好是做空的有利条件，却被当作否决条件。

### 修复方向

`taker_trend` 的判断应该**区分方向**：
- 做多信号（`neg_funding_long`/`crash_bounce_long`）：需要 taker_trend ≥ +5%（主动买入强）
- 做空信号（`pos_funding_short`/`pump_short`）：需要 taker_trend ≤ -5%（主动卖出强）

或者改为只对做多信号检查这个条件，做空信号不检查。
