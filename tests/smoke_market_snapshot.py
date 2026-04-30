#!/usr/bin/env python3
"""Smoke test for Phase 1 market snapshot layer."""
from market_snapshot import get_market_snapshot


def main():
    for sym in ["BTCUSDT", "PRLUSDT"]:
        s = get_market_snapshot(sym)
        print(
            sym,
            "price=", s["price"],
            "atr=", s["atr_pct"],
            "taker=", s["taker_ratio"],
            "oi1h=", s["oi_1h_change"],
            "depth=", s["depth_imbalance"],
        )
        assert s["symbol"] == sym
        assert s["price"] >= 0
        assert "atr_pct" in s
        assert "global_lsr" in s
        assert "top_lsr" in s
        assert "taker_trend_pct" in s
        assert "depth_imbalance" in s
    print("PHASE1_SMOKE_OK")


if __name__ == "__main__":
    main()
