## Issue #1: taker_trend <= -5% direction logic was reversed

**Severity:** High
**File:** `decision_pipeline.py`
**Status:** Fixed

### Problem

Short candidates such as `pos_funding_short` could be rejected when `taker_trend_pct`
was strongly negative. A negative taker trend means active selling pressure is rising,
which is normally favorable for short strategies.

The old logic treated this as a universal veto:

```python
taker_trend = snapshot.get("taker_trend_pct", 0) or 0
if taker_trend <= -5:
    return f"taker trend={taker_trend}% <= -5%"
```

### Fix

The entry veto now reads the signal direction:

- Long signals are rejected when taker trend is strongly negative.
- Short signals are rejected when taker trend is strongly positive.
- Strong negative taker trend is allowed for short candidates.

Smoke coverage was added in `tests/smoke_decision_pipeline.py` to protect all three
cases.
