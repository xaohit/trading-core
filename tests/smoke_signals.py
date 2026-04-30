#!/usr/bin/env python3
"""Smoke test for Phase 2 signals scoring layer."""
from market_snapshot import get_market_snapshot
from signals import analyze


def main():
    for sym in ["BTCUSDT", "PRLUSDT"]:
        snap = get_market_snapshot(sym)
        result = analyze(snap, heat_score=0)
        print(sym, result["score"], result["verdict"], result["tags"][:6])
        assert 0 <= result["score"] <= 100
        assert result["verdict"]
        assert isinstance(result["tags"], list)
        assert isinstance(result["notes"], list)
        assert "oi_divergence" in result
    print("PHASE2_SMOKE_OK")


if __name__ == "__main__":
    main()
