"""
Phase 5 — Social Heat Layer

Fetches Binance Square posts, extracts token mentions, computes
time-decayed heat scores, and produces a 15-minute leaderboard.

No Playwright dependency — uses direct HTTP requests to Binance Square
public API with proxy support.

Expose:
    from social_heat import get_heat_leaderboard
    leaderboard = get_heat_leaderboard(window_minutes=15, top_n=20)
    # → [{"rank":1, "symbol":"BTCUSDT", "heat":85.3, "mentions":12, ...}, ...]
"""
import re
import time
import threading
import subprocess
import json
from datetime import datetime, timezone, timedelta

try:
    from .config import PROXY, PROXIES, EXCLUDE_SYMBOLS
except ImportError:
    from config import PROXY, PROXIES, EXCLUDE_SYMBOLS

# === Configuration ===
SCRAPE_ROUND_SECONDS = 120
HEAT_WINDOW_MINUTES = 15
HEAT_HALF_LIFE_HOURS = 0.25
WEIGHT_LIKE = 1
WEIGHT_COMMENT = 3
WEIGHT_SHARE = 5
MIN_POST_LIKES = 2
MIN_POST_COMMENTS = 1

# Token mention regex: $BTC, #BTC formats
TOKEN_MENTION_RE = re.compile(r'[$#]([A-Z]{2,12})\b')

# Excluded tokens (noise, non-tradable, or ambiguous)
EXCLUDED_TOKENS = {
    "USDT", "BUSD", "USDC", "TUSD", "DAI", "USD", "BTCST",
    "UP", "DOWN", "BULL", "BEAR", "ETH2", "BSC", "CEX",
    "GMX", "APE", "API", "NEW", "KEY", "OLD", "ALL", "NOT",
    "THE", "FOR", "AND", "BUY", "SELL", "HODL", "PUMP",
    "MOON", "FUD", "FOMO", "DYOR", "NFA", "TA", "ROI",
    "CEO", "NGMI", "WAGMI", "GM", "GN", "DEGEN",
}

# Tracked tokens: if not on Binance Square, match bare symbols
_TRACKED_TOKENS: set | None = None

# Cache
_heat_cache: dict | None = None
_cache_ts: float = 0
_cache_lock = threading.Lock()
CACHE_TTL_SECONDS = 120

# Binance Square API config
BINANCE_SQUARE_FEED = "https://www.binance.com/bapi/content/v2/public/homepage/category/article/page"
BINANCE_SQUARE_SEARCH = "https://www.binance.com/bapi/content/v1/public/info/search"


def _curl_post(url: str, payload: dict, timeout: int = 15) -> dict | None:
    """POST request via curl with proxy."""
    try:
        body = json.dumps(payload)
        cmd = [
            "curl", "-s", "--max-time", str(timeout),
            "--proxy", PROXY,
            "-X", "POST",
            "-H", "Content-Type: application/json",
            "-H", "Accept: application/json",
            "-d", body,
            url,
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout + 5
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        data = json.loads(result.stdout)
        if isinstance(data, dict) and "code" in data and data.get("code") != "000000":
            return None
        return data
    except Exception:
        return None


def _fetch_square_posts(limit: int = 30) -> list[dict]:
    """Fetch recent posts from Binance Square feed."""
    posts = []

    # Try feed endpoint
    payload = {
        "catalogId": "0",
        "pageNo": 1,
        "pageSize": limit,
        "language": "en",
    }
    data = _curl_post(BINANCE_SQUARE_FEED, payload)
    if data:
        # Navigate nested response structure
        items = (
            data.get("data", {})
            .get("articles", data.get("data", {}))
        )
        if isinstance(items, list):
            posts.extend(items)
        elif isinstance(items, dict) and "list" in items:
            posts.extend(items["list"])

    # Fallback: try alternative endpoint structure
    if not posts:
        payload2 = {
            "type": "0",
            "pageNo": 1,
            "pageSize": limit,
        }
        data2 = _curl_post(
            "https://www.binance.com/bapi/content/v1/public/homepage/info",
            payload2,
        )
        if data2:
            raw_items = data2.get("data", {}).get("feedList", [])
            if isinstance(raw_items, list):
                posts.extend(raw_items)

    return posts


def _extract_tokens(content: str) -> set[str]:
    """Extract token symbols from post content."""
    if not content:
        return set()
    matches = TOKEN_MENTION_RE.findall(content.upper())
    tokens = set()
    for m in matches:
        if m in EXCLUDED_TOKENS:
            continue
        if len(m) < 2 or len(m) > 12:
            continue
        tokens.add(m)
    return tokens


def _is_human_post(author: dict, post: dict) -> bool:
    """Simple bot/marketing filter based on ZAIJIN88 approach."""
    if not author:
        return True  # Unknown, allow

    # Big V pass-through
    followers = author.get("followers", 0) or 0
    if followers >= 100000:
        return True

    # Username looks changed (not default binance names)
    username = (author.get("userName", "") or "").lower()
    default_patterns = [
        re.compile(r'^(binance|user|binancian|anonymous)[a-z0-9]{10,}$'),
        re.compile(r'^[a-z0-9]{20,}$'),
        re.compile(r'^\d{6,}$'),
        re.compile(r'^0x[a-f0-9]{12,}$'),
        re.compile(r'^user\d+$'),
    ]
    for pat in default_patterns:
        if pat.match(username):
            # Might be a bot account — check post engagement
            likes = post.get("likeCount", 0) or 0
            comments = post.get("commentCount", 0) or 0
            if likes >= MIN_POST_LIKES or comments >= MIN_POST_COMMENTS:
                return True
            return False

    return True


def _author_sig(author: dict) -> str:
    """Signature for author dedup."""
    if not author:
        return ""
    return str(author.get("id", "") or author.get("userName", ""))


def _content_sig(content: str) -> str:
    """Simple content signature for dedup."""
    if not content:
        return ""
    return content[:80].lower().strip()


def compute_heat(posts: list[dict], window_minutes: int = HEAT_WINDOW_MINUTES,
                 half_life_hours: float = HEAT_HALF_LIFE_HOURS) -> list[dict]:
    """
    Compute heat leaderboard from posts.

    Returns sorted list of [{"symbol": "BTCUSDT", "heat": float,
    "mentions": int, "avg_engagement": float, "top_post": str, ...}, ...]
    """
    now = time.time()
    window_secs = window_minutes * 60
    decay_factor = 0.5 ** (1.0 / (half_life_hours * 3600))

    # Track: token -> list of (score, author_sig, content_sig, engagement)
    token_scores: dict[str, list[dict]] = {}
    # Track author posts per token for same-author downweight
    author_token_count: dict[str, dict[str, int]] = {}

    for post in posts:
        content = post.get("content", "") or post.get("summary", "") or ""
        if not content:
            continue

        # Time decay
        pub_time = post.get("publishTime", 0) or post.get("publish_time", 0)
        if isinstance(pub_time, str):
            try:
                pub_time = int(pub_time)
            except (ValueError, TypeError):
                pub_time = 0
        if pub_time == 0:
            pub_time = now
        else:
            # Binance Square uses milliseconds
            if pub_time > 1e12:
                pub_time = pub_time / 1000
        age_secs = now - pub_time
        if age_secs < 0 or age_secs > window_secs * 4:
            continue  # Too old or invalid
        decay = decay_factor ** age_secs

        # Engagement
        likes = int(post.get("likeCount", 0) or 0)
        comments = int(post.get("commentCount", 0) or 0)
        shares = int(post.get("shareCount", 0) or 0)

        # Author check
        author = post.get("author", post.get("authorInfo", {})) or {}
        if not _is_human_post(author, post):
            continue

        # Extract tokens
        tokens = _extract_tokens(content)
        if not tokens:
            continue

        # Same author downweight
        author_id = _author_sig(author)
        c_sig = _content_sig(content)

        base_score = likes * WEIGHT_LIKE + comments * WEIGHT_COMMENT + shares * WEIGHT_SHARE

        for token in tokens:
            if token not in author_token_count:
                author_token_count[token] = {}
            author_token_count[token][author_id] = author_token_count[token].get(author_id, 0) + 1
            count = author_token_count[token][author_id]

            # Downweight: 3rd+ post from same author on same token
            if count > 2:
                score = base_score * 0.25
            else:
                score = base_score

            # Duplicate content downweight
            if token in token_scores:
                for existing in token_scores[token]:
                    if existing["c_sig"] == c_sig and existing["author_id"] == author_id:
                        score *= 0.35
                        break

            token_scores.setdefault(token, []).append({
                "score": score * decay,
                "author_id": author_id,
                "c_sig": c_sig,
                "likes": likes,
                "comments": comments,
                "content": content[:200] if content else "",
                "pub_time": pub_time,
            })

    # Aggregate per token
    leaderboard = []
    for token, entries in token_scores.items():
        total_heat = sum(e["score"] for e in entries)
        total_mentions = len(entries)
        avg_engagement = sum(e["likes"] + e["comments"] for e in entries) / total_mentions if total_mentions > 0 else 0

        # Find top post
        top_entry = max(entries, key=lambda e: e["score"])

        symbol = token + "USDT"
        if symbol in EXCLUDE_SYMBOLS:
            continue

        leaderboard.append({
            "symbol": symbol,
            "token": token,
            "heat": round(total_heat, 2),
            "mentions": total_mentions,
            "avg_engagement": round(avg_engagement, 2),
            "top_post": top_entry["content"],
            "top_post_time": datetime.fromtimestamp(top_entry["pub_time"], tz=timezone.utc).strftime("%H:%M"),
        })

    leaderboard.sort(key=lambda x: x["heat"], reverse=True)
    for i, entry in enumerate(leaderboard):
        entry["rank"] = i + 1

    return leaderboard


def get_heat_leaderboard(window_minutes: int = HEAT_WINDOW_MINUTES,
                         top_n: int = 20, force_refresh: bool = False) -> list[dict]:
    """
    Get social heat leaderboard with caching.

    Args:
        window_minutes: Time window for heat calculation
        top_n: Number of top tokens to return
        force_refresh: Bypass cache and re-fetch

    Returns:
        List of heat entries sorted by heat score
    """
    global _heat_cache, _cache_ts

    now = time.time()
    with _cache_lock:
        if not force_refresh and _heat_cache is not None and (now - _cache_ts) < CACHE_TTL_SECONDS:
            return _heat_cache[:top_n]

    # Fetch and compute
    posts = _fetch_square_posts(limit=50)
    if posts:
        leaderboard = compute_heat(posts, window_minutes=window_minutes)
    else:
        # API unavailable — return empty
        leaderboard = []

    with _cache_lock:
        _heat_cache = leaderboard
        _cache_ts = now

    return leaderboard[:top_n]


def get_heat_for_symbol(symbol: str) -> dict | None:
    """Get heat score for a specific symbol."""
    lb = get_heat_leaderboard()
    base = symbol.replace("USDT", "")
    for entry in lb:
        if entry.get("symbol") == symbol or entry.get("token") == base:
            return entry
    return None


def get_candidate_symbols(top_n: int = 15) -> list[str]:
    """
    Get candidate symbols from heat leaderboard.
    Returns list of symbols (e.g. ["BTCUSDT", "ETHUSDT", ...])
    sorted by heat descending.
    """
    lb = get_heat_leaderboard(top_n=top_n)
    return [entry["symbol"] for entry in lb]
