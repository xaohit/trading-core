"""
Market data fetcher — Binance Futures
所有请求统一走 requests 库（HTTP/HTTPS proxy），不再用 curl subprocess
"""
import json
import time
import hashlib
import hmac as _hmac
import threading
from typing import Optional

import requests

try:
    from config import PROXY, PROXIES, BINANCE_API_KEY, BINANCE_API_SECRET
except ImportError:
    from config import PROXY, PROXIES, BINANCE_API_KEY, BINANCE_API_SECRET  # pragma: no cover

FAPI_BASE = "https://fapi.binance.com"
FGI_URL = "https://api.alternative.me/fng/"

# ── FGI 缓存 ──────────────────────────────────────────────────────────────
_fgi_cache = {"value": None, "timestamp": 0}
_fgi_lock = threading.Lock()


def _get(url: str, timeout: int = 15) -> Optional[dict | list]:
    """GET public endpoint."""
    try:
        r = requests.get(url, timeout=timeout, proxies=PROXIES)
        if r.status_code != 200:
            return None
        data = r.json()
        # Binance error responses: {"code": -1003, "msg": "..."}
        if isinstance(data, dict) and "code" in data and data["code"] < 0:
            return None
        return data
    except Exception:
        return None


def _signed_get(endpoint: str, params: dict) -> Optional[dict]:
    """GET signed endpoint — API key via header, never in shell."""
    params["timestamp"] = int(time.time() * 1000)
    query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    signature = _hmac.new(
        BINANCE_API_SECRET.encode(), query.encode(), hashlib.sha256
    ).hexdigest()
    url = f"{FAPI_BASE}{endpoint}?{query}&signature={signature}"
    try:
        r = requests.get(
            url,
            headers={"X-MBX-APIKEY": BINANCE_API_KEY},
            timeout=15,
            proxies=PROXIES,
        )
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


# ── 向后兼容：原有 _curl_get 改为 requests 实现 ──────────────────────────
# market_snapshot.py / backtest.py 仍引用此函数
_curl_get = _get


class Market:
    """市场数据获取"""

    @staticmethod
    def all_tickers() -> list:
        data = _get(f"{FAPI_BASE}/fapi/v1/ticker/24hr")
        if not isinstance(data, list):
            return []
        return [t for t in data if t.get("symbol", "").endswith("USDT")]

    @staticmethod
    def ticker(symbol: str) -> Optional[dict]:
        data = _get(f"{FAPI_BASE}/fapi/v1/ticker/24hr?symbol={symbol}")
        return data if isinstance(data, dict) and "symbol" in data else None

    @staticmethod
    def funding_rates() -> dict:
        data = _get(f"{FAPI_BASE}/fapi/v1/premiumIndex")
        if not isinstance(data, list):
            return {}
        rates = {}
        for t in data:
            if not isinstance(t, dict):
                continue
            try:
                rates[t["symbol"]] = float(t["lastFundingRate"]) * 100
            except (KeyError, TypeError, ValueError):
                continue
        return rates

    @staticmethod
    def open_interest(symbol: str) -> float:
        data = _get(f"{FAPI_BASE}/fapi/v1/openInterest?symbol={symbol}")
        if not isinstance(data, dict):
            return 0.0
        try:
            return float(data["openInterest"])
        except (KeyError, TypeError, ValueError):
            return 0.0

    @staticmethod
    def klines(symbol: str, interval: str = "1h", limit: int = 100) -> list:
        url = (f"{FAPI_BASE}/fapi/v1/klines"
               f"?symbol={symbol}&interval={interval}&limit={limit}")
        data = _get(url)
        return data if isinstance(data, list) else []

    @staticmethod
    def balance() -> float:
        data = _signed_get("/fapi/v2/account", {})
        if not data:
            return 40.0
        for asset in data.get("assets", []):
            if asset.get("asset") == "USDT":
                try:
                    return max(float(asset.get("availableBalance", 0)), 40.0)
                except (TypeError, ValueError):
                    pass
        return 40.0

    @staticmethod
    def fear_greed_index() -> Optional[int]:
        now = time.time()
        with _fgi_lock:
            if _fgi_cache["value"] is not None and (now - _fgi_cache["timestamp"]) < 300:
                return _fgi_cache["value"]
        try:
            r = requests.get(FGI_URL, timeout=5, proxies=PROXIES)
            if r.status_code == 200:
                val = int(r.json()["data"][0]["value"])
                with _fgi_lock:
                    _fgi_cache["value"] = val
                    _fgi_cache["timestamp"] = now
                return val
        except Exception:
            pass
        with _fgi_lock:
            return _fgi_cache["value"]
