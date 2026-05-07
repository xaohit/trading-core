"""
data_fetcher.py — 异步并发拉数据，超时隔离
"""
import asyncio, aiohttp, json, ssl
from typing import Any

# Binance 无需 API key 的公开端点
ENDPOINTS = {
    "tickers": "https://fapi.binance.com/fapi/v1/ticker/24hr",
    "funding": "https://fapi.binance.com/fapi/v1/premiumIndex",
    "fear_greed": "https://api.alternative.me/fng/",
}

TIMEOUT = 10  # 秒，任何请求超过此时间直接放弃
PROXY = "http://localhost:7897"

# 信任本地代理的 MITM 证书
_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE


async def _fetch(session: aiohttp.ClientSession, key: str) -> tuple[str, Any]:
    """拉单个数据源，超时返回 (key, None)"""
    url = ENDPOINTS[key]
    try:
        async with session.get(
            url,
            proxy=PROXY,
            timeout=aiohttp.ClientTimeout(total=TIMEOUT),
        ) as resp:
            if resp.status != 200:
                return key, None
            data = await resp.json()
            return key, data
    except Exception:
        return key, None


async def fetch_all() -> tuple[list, dict, Any]:
    """
    并发拉所有数据源。
    任一失败不影响其他。
    Returns: (tickers, funding_rates, fear_greed)
    """
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=_ssl_ctx)) as session:
        results = await asyncio.gather(
            _fetch(session, "tickers"),
            _fetch(session, "funding"),
            _fetch(session, "fear_greed"),
            return_exceptions=True,
        )

    tickers = []
    funding_rates = {}
    fear_greed = None

    for r in results:
        if isinstance(r, Exception):
            continue
        key, data = r
        if data is None:
            continue
        if key == "tickers":
            tickers = [t for t in data if t.get("symbol", "").endswith("USDT")]
        elif key == "funding":
            if isinstance(data, list):
                funding_rates = {d["symbol"]: float(d["lastFundingRate"]) for d in data}
            elif isinstance(data, dict):
                funding_rates = {data["symbol"]: float(data["lastFundingRate"])}
        elif key == "fear_greed":
            fear_greed = data

    return tickers, funding_rates, fear_greed
