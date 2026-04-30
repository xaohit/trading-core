"""
Narrative Radar — Token momentum scanner for Binance Futures

Data source: Binance USD-M Futures (no external API needed)
Momentum is the only push engine. Narratives are classification labels only.
Runs every 30s via daemon.

Narrative tags: musk / trump / binance / celebrity / ai / meme / defi
"""

import time
import logging
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional

try:
    from ..config import PROXIES
    from ..market import Market
except ImportError:
    from config import PROXIES
    from market import Market

log = logging.getLogger(__name__)
TZ_UTC8 = timezone(timedelta(hours=8))

# ─── Narrative classification ────────────────────────────────────
NARRATIVE_KEYWORDS = {
    "musk":      ["doge", "shib", "floki", "elon", "bone"],
    "trump":     ["trump", "maga", "miggle", "freight"],
    "binance":   ["bnb", "cake", "bakery", "bis"],
    "celebrity": ["jenner", "kylie", "rihanna", "snoop", "cardi"],
    "ai":        ["ai", "fetch", "agent", "gpt", "llm", "弯", "agentt"],
    "meme":      ["pepe", "wojak", "based", "brett", "michi", "ninja", "inu", "shib"],
    "defi":      ["cake", "pancake", "uniswap", "sushi", "curve", "balancer"],
    "gaming":    ["gala", "mana", "sand", "axie", "enjin"],
}

# Symbols to skip (stablecoins, huge caps)
SKIP_PATTERNS = ["usdt", "usdc", "busd", "tusd", "btc", "eth", "bnb", "wrap"]


@dataclass
class TokenSnapshot:
    chain: str
    address: str
    symbol: str
    market_cap: float
    volume_24h: float
    price: float
    txns_5m_buy: int = 0
    txns_5m_sell: int = 0
    momentum_score: float = 0.0
    narrative_tags: list = field(default_factory=list)
    star_rating: int = 0  # ★★★/★★/★
    safety_ok: bool = True
    safety_details: str = ""
    score_breakdown: dict = field(default_factory=dict)


def classify_narrative(symbol: str, address: str) -> tuple[list, int]:
    """Returns (tags, star_rating)"""
    s = f"{symbol} {address}".lower()
    matched = []
    for tag, keywords in NARRATIVE_KEYWORDS.items():
        if any(k in s for k in keywords):
            matched.append(tag)
    # star rating based on narrative type
    star_map = {"musk": 3, "trump": 3, "binance": 2, "celebrity": 2, "ai": 2, "meme": 1}
    stars = max((star_map.get(t, 1) for t in matched), default=1)
    return matched, stars


def check_safety_sol(address: str) -> tuple[bool, str]:
    """RugCheck for Solana tokens."""
    try:
        url = RUGCHECK_SOL.format(address)
        r = requests.get(url, timeout=8, proxies=PROXIES)
        if r.status_code != 200:
            return True, "rugcheck_unavailable"
        data = r.json()
        # Top risk flag
        if data.get("token", {}).get("riskLevel") in ("DANGER", "WARNING"):
            return False, f"rugcheck: {data.get('token', {}).get('riskLevel')}"
        return True, "ok"
    except Exception as e:
        return True, f"rugcheck_err:{e}"


def check_safety_evm(chain_id: int, address: str) -> tuple[bool, str]:
    """GoPlus for EVM chains. chain_id: 1=ETH, 56=BSC, 8453=Base"""
    try:
        url = f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}?contract_addresses={address}"
        r = requests.get(url, timeout=8, proxies=PROXIES)
        if r.status_code != 200:
            return True, "goplus_unavailable"
        data = r.json()
        result = data.get("result", {})
        if not result:
            return True, "ok"
        token_info = result.get(address.lower(), {})
        # Honeypot check
        if token_info.get("is_honeypot") == "1":
            return False, "honeypot"
        # Freezed / locked pools
        if token_info.get("is_locked") == "1":
            return False, "locked"
        return True, "ok"
    except Exception as e:
        return True, f"goplus_err:{e}"


def fetch_dexscreener_pairs(chain: str, limit: int = 50) -> list[dict]:
    """Fetch top pairs by volume on a given chain from DEXScreener (no proxy)."""
    try:
        # DEXScreener latest pairs sorted by volume
        url = f"https://api.dexscreener.io/latest/dex/pairs/{chain}?limit={limit}"
        r = _no_proxy_get(url)
        if r.status_code != 200:
            return []
        data = r.json()
        pairs = data.get("pairs", []) or []
        results = []
        for p in pairs:
            mc = float(p.get("marketCap", 0) or 0)
            liquidity = float(p.get("liquidity", {}).get("usd", 0) or 0)
            if mc < 1000 or liquidity < 1000:
                continue
            results.append({
                "chain": chain,
                "chainId": p.get("chainId", chain),
                "address": p.get("baseToken", {}).get("address", ""),
                "symbol": p.get("baseToken", {}).get("symbol", "?"),
                "price": float(p.get("priceUsd", 0) or 0),
                "market_cap": mc,
                "volume_24h": float(p.get("volume", {}).get("h24", 0) or 0),
                "liquidity": liquidity,
                "txns_5m_buy": p.get("txns", {}).get("m5", {}).get("buys", 0) or 0,
                "txns_5m_sell": p.get("txns", {}).get("m5", {}).get("sells", 0) or 0,
                "price_change_5m": p.get("priceChange", {}).get("m5", 0) or 0,
                "price_change_1h": p.get("priceChange", {}).get("h1", 0) or 0,
                "price_change_6h": p.get("priceChange", {}).get("h6", 0) or 0,
                "price_change_24h": p.get("priceChange", {}).get("h24", 0) or 0,
            })
        return results
    except Exception as e:
        log.warning(f"DEXScreener fetch error ({chain}): {e}")
        return []


def _calc_momentum(pairs: list[dict]) -> float:
    """
    Momentum score = consecutive rounds of market cap increase with ≥5% total gain.
    Checks 5m → 1h → 6h price changes.
    """
    if not pairs:
        return 0.0
    # Use the pair with highest liquidity
    best = max(pairs, key=lambda x: x.get("liquidity", 0))

    m5 = best.get("price_change_5m", 0)
    m1 = best.get("price_change_1h", 0)
    m6 = best.get("price_change_6h", 0)

    rounds = 0
    if m5 >= 5:
        rounds += 1
        if m1 >= 5:
            rounds += 1
            if m6 >= 5:
                rounds += 1

    # Score: 3 rounds = 10, 2 rounds = 6, 1 round = 3
    score_map = {3: 10.0, 2: 6.0, 1: 3.0, 0: 0.0}
    return score_map.get(rounds, 0.0)


def scan_narrative_tokens(top_n: int = 30) -> list[TokenSnapshot]:
    """
    Main scan: fetch trending/pumped tokens from DEXscreener, apply momentum + safety filters.
    Covers: Ethereum / Solana / BSC / Base

    Momentum trigger: 3 consecutive rounds of ≥5% price increase (5m → 1h → 6h).
    Only momentum triggers alerts. Narratives are classification labels only.
    Safety checks: RugCheck (SOL), GoPlus (EVM chains).
    """
    results: list[TokenSnapshot] = []

    chains = [
        ("ethereum", "Ethereum", 1),    # chain_id for GoPlus
        ("solana",   "Solana",   0),    # 0 = not used (RugCheck uses address)
        ("bsc",      "BNB Chain", 56),
        ("base",     "Base",     8453),
    ]

    for chain_key, chain_name, chain_id in chains:
        pairs = fetch_dexscreener_pairs(chain_key, limit=top_n)
        if not pairs:
            continue

        for p in pairs:
            address = p.get("address", "")
            symbol  = p.get("symbol", "?")
            mc      = p.get("market_cap", 0)
            vol     = p.get("volume_24h", 0)
            price   = p.get("price", 0)
            p5      = p.get("price_change_5m", 0)
            p1      = p.get("price_change_1h", 0)
            p6      = p.get("price_change_6h", 0)
            p24     = p.get("price_change_24h", 0)

            if not address or mc < 1000:
                continue

            # ── Narrative classification ──────────────────────────────────
            tags, stars = classify_narrative(symbol, address)

            # ── Safety check ─────────────────────────────────────────────
            if chain_key == "solana":
                safety_ok, safety_details = check_safety_sol(address)
            else:
                safety_ok, safety_details = check_safety_evm(chain_id, address)

            if not safety_ok:
                continue

            # ── Momentum: consecutive rounds of ≥5% ──────────────────────
            rounds = 0
            if p5 >= 5:
                rounds += 1
                if p1 >= 5:
                    rounds += 1
                    if p6 >= 5:
                        rounds += 1

            momentum = {3: 10.0, 2: 6.0, 1: 3.0, 0: 0.0}.get(rounds, 0.0)

            # Skip tokens with no momentum
            if momentum == 0:
                continue

            # Calculate buy pressure
            buys  = p.get("txns_5m_buy", 0)
            sells = p.get("txns_5m_sell", 0)
            buy_pressure = buys / max(buys + sells, 1) * 100

            snap = TokenSnapshot(
                chain=chain_name,
                address=address,
                symbol=symbol,
                market_cap=mc,
                volume_24h=vol,
                price=price,
                txns_5m_buy=buys,
                txns_5m_sell=sells,
                momentum_score=momentum,
                narrative_tags=tags,
                star_rating=stars,
                safety_ok=safety_ok,
                safety_details=safety_details,
                score_breakdown={
                    "rounds": rounds,
                    "price_change_5m": p5,
                    "price_change_1h": p1,
                    "price_change_6h": p6,
                    "price_change_24h": p24,
                    "liquidity": p.get("liquidity", 0),
                    "buy_pressure": buy_pressure,
                }
            )
            results.append(snap)

        # Avoid hammering the API
        time.sleep(0.5)

    results.sort(key=lambda x: x.momentum_score, reverse=True)
    return results


def format_alert(ts: TokenSnapshot) -> str:
    """Format a narrative alert as a TG-ready message."""
    stars = "★" * ts.star_rating
    tags_str = "/".join(ts.narrative_tags) if ts.narrative_tags else "momentum"
    bp = ts.score_breakdown.get("buy_pressure", 0)

    return f"""🔔 叙事雷达
{ts.symbol} / {ts.chain}
市值: ${ts.market_cap:,.0f} | 24h量: ${ts.volume_24h:,.0f}
标签: {tags_str} {stars}
momentum: {ts.momentum_score:.0f}/10 ({ts.score_breakdown['rounds']}轮)

5m +{ts.score_breakdown['price_change_5m']:+.1f}% | 1h +{ts.score_breakdown['price_change_1h']:+.1f}% | 6h +{ts.score_breakdown['price_change_6h']:+.1f}%
买压: {bp:.0f}% | 池子: ${ts.score_breakdown.get('liquidity', 0):,.0f}
地址: `{ts.address}`"""


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    print(f"[{datetime.now(TZ_UTC8).strftime('%m-%d %H:%M:%S')}] Scanning...")
    snaps = scan_narrative_tokens(top_n=30)
    print(f"Found {len(snaps)} momentum tokens")
    for s in snaps[:5]:
        print()
        print(format_alert(s))
