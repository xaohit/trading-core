"""
Market Snapshot Layer — ZAIJIN88-inspired Binance USD-M futures metrics.

Safe by design:
- Public endpoints only.
- Missing/invalid Binance responses degrade to None/0, not exceptions.
- No trading side effects.
"""
from __future__ import annotations

import math
from typing import Any, Optional

try:
    from .market import _curl_get, Market
except ImportError:
    from market import _curl_get, Market


BASE = "https://fapi.binance.com"


def _to_float(v: Any, default: Optional[float] = 0.0) -> Optional[float]:
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _safe_list(data: Any) -> list:
    return data if isinstance(data, list) else []


def _pct_change(old: Optional[float], new: Optional[float]) -> Optional[float]:
    if old is None or new is None or old == 0:
        return None
    return round((new - old) / old * 100, 4)


def _klines(symbol: str, interval: str, limit: int) -> list:
    return Market.klines(symbol, interval, limit)


def _change_from_klines(symbol: str) -> dict:
    """15m/1h/4h percentage changes based on 5m klines."""
    kl = _klines(symbol, "5m", 49)  # 4h + current
    closes = [_to_float(k[4], None) for k in kl if isinstance(k, list) and len(k) >= 5]
    closes = [c for c in closes if c is not None]
    if not closes:
        return {"change_15m": None, "change_1h": None, "change_4h": None}
    cur = closes[-1]

    def ago(n_bars: int):
        return closes[-1 - n_bars] if len(closes) > n_bars else None

    return {
        "change_15m": _pct_change(ago(3), cur),
        "change_1h": _pct_change(ago(12), cur),
        "change_4h": _pct_change(ago(48), cur),
    }


def _atr_pct(symbol: str, interval: str = "1h", limit: int = 15) -> Optional[float]:
    """ATR percentage using standard True Range over recent klines."""
    kl = _klines(symbol, interval, limit)
    rows = []
    for k in kl:
        if not isinstance(k, list) or len(k) < 5:
            continue
        rows.append({
            "high": _to_float(k[2], None),
            "low": _to_float(k[3], None),
            "close": _to_float(k[4], None),
        })
    rows = [r for r in rows if None not in r.values()]
    if len(rows) < 2:
        return None

    trs = []
    prev_close = rows[0]["close"]
    for r in rows[1:]:
        tr = max(
            r["high"] - r["low"],
            abs(r["high"] - prev_close),
            abs(r["low"] - prev_close),
        )
        trs.append(tr)
        prev_close = r["close"]
    if not trs or rows[-1]["close"] == 0:
        return None
    atr = sum(trs) / len(trs)
    return round(atr / rows[-1]["close"] * 100, 4)


def _oi_hist(symbol: str, period: str = "5m", limit: int = 49) -> dict:
    data = _curl_get(
        f"{BASE}/futures/data/openInterestHist?symbol={symbol}&period={period}&limit={limit}",
        timeout=10,
    )
    rows = _safe_list(data)
    vals = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        # Binance can use sumOpenInterest or sumOpenInterestValue depending endpoint version.
        v = _to_float(r.get("sumOpenInterest"), None)
        vals.append(v)
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"oi_15m_change": None, "oi_1h_change": None, "oi_4h_change": None}
    cur = vals[-1]

    def ago(n_bars: int):
        return vals[-1 - n_bars] if len(vals) > n_bars else None

    return {
        "oi_15m_change": _pct_change(ago(3), cur),
        "oi_1h_change": _pct_change(ago(12), cur),
        "oi_4h_change": _pct_change(ago(48), cur),
    }


def _ratio_endpoint(path: str, symbol: str, period: str = "5m", limit: int = 4) -> list:
    return _safe_list(_curl_get(f"{BASE}{path}?symbol={symbol}&period={period}&limit={limit}", timeout=10))


def _latest_ratio(rows: list, key: str = "longShortRatio") -> Optional[float]:
    for r in reversed(rows):
        if isinstance(r, dict) and key in r:
            return _to_float(r.get(key), None)
    return None


def _taker_flow(symbol: str) -> dict:
    rows = _ratio_endpoint("/futures/data/takerlongshortRatio", symbol, "5m", 4)
    vals = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        vals.append(_to_float(r.get("buySellRatio"), None))
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"taker_ratio": None, "taker_trend_pct": None}
    latest = vals[-1]
    prev = vals[:-1]
    trend = None
    if prev:
        avg_prev = sum(prev) / len(prev)
        trend = _pct_change(avg_prev, latest)
    return {"taker_ratio": round(latest, 4), "taker_trend_pct": trend}


def _depth_1pct(symbol: str, price: float) -> dict:
    data = _curl_get(f"{BASE}/fapi/v1/depth?symbol={symbol}&limit=100", timeout=10)
    if not isinstance(data, dict) or price <= 0:
        return {"depth_bid_usd_1pct": None, "depth_ask_usd_1pct": None, "depth_imbalance": None}

    bid_floor = price * 0.99
    ask_ceiling = price * 1.01
    bid_usd = 0.0
    ask_usd = 0.0

    for p, q in data.get("bids", []) or []:
        pf = _to_float(p, None)
        qf = _to_float(q, None)
        if pf is not None and qf is not None and pf >= bid_floor:
            bid_usd += pf * qf
    for p, q in data.get("asks", []) or []:
        pf = _to_float(p, None)
        qf = _to_float(q, None)
        if pf is not None and qf is not None and pf <= ask_ceiling:
            ask_usd += pf * qf

    total = bid_usd + ask_usd
    imbalance = ((bid_usd - ask_usd) / total * 100) if total > 0 else None
    return {
        "depth_bid_usd_1pct": round(bid_usd, 2),
        "depth_ask_usd_1pct": round(ask_usd, 2),
        "depth_imbalance": round(imbalance, 2) if imbalance is not None else None,
    }


def get_market_snapshot(symbol: str) -> dict:
    """Return a unified market snapshot for one Binance USD-M symbol."""
    symbol = symbol.upper().strip()

    ticker = Market.ticker(symbol) or {}
    price = _to_float(ticker.get("lastPrice"), 0.0) or 0.0
    change_24h = _to_float(ticker.get("priceChangePercent"), None)
    quote_volume_24h = _to_float(ticker.get("quoteVolume"), 0.0) or 0.0

    premium = _curl_get(f"{BASE}/fapi/v1/premiumIndex?symbol={symbol}", timeout=10)
    funding_rate = None
    if isinstance(premium, dict):
        fr = _to_float(premium.get("lastFundingRate"), None)
        funding_rate = round(fr * 100, 6) if fr is not None else None

    global_lsr = _latest_ratio(_ratio_endpoint("/futures/data/globalLongShortAccountRatio", symbol))
    top_lsr = _latest_ratio(_ratio_endpoint("/futures/data/topLongShortPositionRatio", symbol))

    snapshot = {
        "symbol": symbol,
        "price": price,
        "change_24h": change_24h,
        "quote_volume_24h": quote_volume_24h,
        "oi": Market.open_interest(symbol),
        "funding_rate": funding_rate,
        "global_lsr": global_lsr,
        "top_lsr": top_lsr,
        "atr_pct": _atr_pct(symbol),
    }
    snapshot.update(_change_from_klines(symbol))
    snapshot.update(_oi_hist(symbol))
    snapshot.update(_taker_flow(symbol))
    snapshot.update(_depth_1pct(symbol, price))
    return snapshot


if __name__ == "__main__":
    import json
    import sys

    sym = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    print(json.dumps(get_market_snapshot(sym), ensure_ascii=False, indent=2))
