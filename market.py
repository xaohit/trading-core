"""
Market data fetcher — Binance Futures
"""
import subprocess
import json
import requests
import time
import threading
from typing import Optional

try:
    from .config import PROXY, PROXIES, BINANCE_API_KEY, BINANCE_API_SECRET
except ImportError:
    from config import PROXY, PROXIES, BINANCE_API_KEY, BINANCE_API_SECRET

# FGI 缓存：5分钟有效
_fgi_cache = {"value": None, "timestamp": 0}
_fgi_lock = threading.Lock()


def _curl_get(url: str, timeout: int = 15):
    cmd = ["curl", "-s", "--max-time", str(timeout), "--proxy", PROXY, url]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout + 5
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        data = json.loads(result.stdout)
        # Binance error responses are dicts like {"code": -1003, "msg": "..."}
        if isinstance(data, dict) and "code" in data and "msg" in data:
            return None
        return data
    except Exception:
        return None


def _signed_get(endpoint: str, params: dict) -> Optional[dict]:
    import time, hmac, hashlib
    timestamp = int(time.time() * 1000)
    params["timestamp"] = timestamp
    query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    signature = hmac.new(
        BINANCE_API_SECRET.encode(), query.encode(), hashlib.sha256
    ).hexdigest()
    query += f"&signature={signature}"
    url = f"https://fapi.binance.com{endpoint}?{query}"
    cmd = ["curl", "-s", "--max-time", "15", "--proxy", PROXY,
           "-H", f"X-MBX-APIKEY: {BINANCE_API_KEY}", url]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=20
        )
        return json.loads(result.stdout) if result.returncode == 0 else None
    except:
        return None


class Market:
    """市场数据获取"""

    @staticmethod
    def all_tickers() -> list:
        """所有USDT合约24h行情"""
        data = _curl_get("https://fapi.binance.com/fapi/v1/ticker/24hr")
        if not isinstance(data, list):
            return []
        return [t for t in data if t.get("symbol", "").endswith("USDT")]

    @staticmethod
    def ticker(symbol: str) -> Optional[dict]:
        """单个币种行情"""
        data = _curl_get(
            f"https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={symbol}"
        )
        return data if isinstance(data, dict) and "symbol" in data else None

    @staticmethod
    def funding_rates() -> dict:
        """所有币种资金费率 {symbol: rate%}"""
        data = _curl_get("https://fapi.binance.com/fapi/v1/premiumIndex")
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
        """获取币种持仓量"""
        data = _curl_get(
            f"https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol}"
        )
        if not isinstance(data, dict):
            return 0.0
        try:
            return float(data["openInterest"])
        except (KeyError, TypeError, ValueError):
            return 0.0

    @staticmethod
    def klines(symbol: str, interval: str = "1h", limit: int = 100) -> list:
        """K线数据"""
        url = (f"https://fapi.binance.com/fapi/v1/klines"
               f"?symbol={symbol}&interval={interval}&limit={limit}")
        data = _curl_get(url)
        return data if isinstance(data, list) else []

    @staticmethod
    def balance() -> float:
        """账户余额"""
        data = _signed_get("/fapi/v2/account", {"leverage": 3})
        if not data:
            return 40.0
        for asset in data.get("assets", []):
            if asset["asset"] == "USDT":
                return max(float(asset["availableBalance"]), 40.0)
        return 40.0

    @staticmethod
    def fear_greed_index() -> Optional[int]:
        """Fear & Greed Index，缓存5分钟"""
        now = time.time()
        with _fgi_lock:
            if _fgi_cache["value"] is not None and (now - _fgi_cache["timestamp"]) < 300:
                return _fgi_cache["value"]
        try:
            r = requests.get(
                "https://api.alternative.me/fng/", timeout=5, proxies=PROXIES
            )
            if r.status_code == 200:
                val = int(r.json()["data"][0]["value"])
                with _fgi_lock:
                    _fgi_cache["value"] = val
                    _fgi_cache["timestamp"] = now
                return val
        except:
            pass
        with _fgi_lock:
            return _fgi_cache["value"]
