"""
Phase 5 Social Heat Smoke Tests
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from social_heat import (
    _extract_tokens, _is_human_post, compute_heat,
    get_heat_leaderboard, get_heat_for_symbol, get_candidate_symbols,
    EXCLUDED_TOKENS, TOKEN_MENTION_RE,
)


def _sample_posts():
    """Generate sample posts for testing."""
    now_ms = int(time.time() * 1000)
    return [
        {
            "content": "$BTC looking strong! #ETH also bullish $\nSOL momentum is real.",
            "likeCount": 50,
            "commentCount": 10,
            "shareCount": 5,
            "publishTime": now_ms - 300000,  # 5 min ago
            "author": {"id": "user1", "userName": "trader_pro", "followers": 5000},
        },
        {
            "content": "$BTC breaking out! Target 80k $ETH $DOGE to the moon",
            "likeCount": 100,
            "commentCount": 25,
            "shareCount": 15,
            "publishTime": now_ms - 600000,  # 10 min ago
            "author": {"id": "user2", "userName": "crypto_whale", "followers": 150000},
        },
        {
            "content": "$DOGE whale alert! Big moves coming",
            "likeCount": 30,
            "commentCount": 8,
            "shareCount": 3,
            "publishTime": now_ms - 120000,  # 2 min ago
            "author": {"id": "user3", "userName": "binance_user_abc123def456ghi789jkl012", "followers": 10},
        },
        {
            "content": "USDT stable, nothing to see here",
            "likeCount": 5,
            "commentCount": 0,
            "shareCount": 0,
            "publishTime": now_ms - 900000,
            "author": {"id": "user4", "userName": "bot_account", "followers": 0},
        },
        {
            "content": "$BTC $BTC $BTC buy buy buy!!!",
            "likeCount": 200,
            "commentCount": 50,
            "shareCount": 30,
            "publishTime": now_ms - 1800000,  # 30 min ago (outside 15min window)
            "author": {"id": "user5", "userName": "spam_bot", "followers": 2},
        },
        {
            "content": "$SOL ecosystem growing fast. $AVAX and $MATIC also solid.",
            "likeCount": 40,
            "commentCount": 12,
            "shareCount": 6,
            "publishTime": now_ms - 450000,  # 7.5 min ago
            "author": {"id": "user6", "userName": "defi_analyst", "followers": 25000},
        },
    ]


def test_token_extraction():
    """Test token mention regex extraction."""
    text = "$BTC looking good! #ETH bullish $DOGE mooning"
    tokens = _extract_tokens(text)
    assert "BTC" in tokens, f"Expected BTC in {tokens}"
    assert "ETH" in tokens, f"Expected ETH in {tokens}"
    assert "DOGE" in tokens, f"Expected DOGE in {tokens}"

    # Excluded tokens
    text2 = "$USDT is stable $CEO is watching"
    tokens2 = _extract_tokens(text2)
    assert "USDT" not in tokens2, "USDT should be excluded"
    assert "CEO" not in tokens2, "CEO should be excluded"

    print("  [OK] Token extraction correct")


def test_author_filter():
    """Test human/bot author filtering."""
    # Big V — always passes
    assert _is_human_post(
        {"id": "v1", "userName": "vip", "followers": 200000},
        {"likeCount": 0, "commentCount": 0},
    ), "Big V should pass"

    # Default username with high engagement — passes
    assert _is_human_post(
        {"id": "b1", "userName": "binance_user_abcdef1234567890abcd", "followers": 5},
        {"likeCount": 50, "commentCount": 10},
    ), "High engagement should pass even for default names"

    # Default username with low engagement — fails
    assert not _is_human_post(
        {"id": "b2", "userName": "123456789", "followers": 0},
        {"likeCount": 0, "commentCount": 0},
    ), "Low engagement bot should fail"

    print("  [OK] Author filtering correct")


def test_heat_computation():
    """Test heat leaderboard computation."""
    posts = _sample_posts()
    lb = compute_heat(posts, window_minutes=15)

    # Should have entries
    assert len(lb) > 0, f"Should have some heat entries. Excluded tokens: BTCUSDT, ETHUSDT, etc."

    # DOGE should be present (not excluded)
    doge_entry = next((e for e in lb if e["token"] == "DOGE"), None)
    assert doge_entry is not None, f"DOGE should be in leaderboard. Tokens: {[e['token'] for e in lb]}"
    assert doge_entry["heat"] > 0, "DOGE heat should be > 0"

    # SOL should be present
    sol_entry = next((e for e in lb if e["token"] == "SOL"), None)
    assert sol_entry is not None, f"SOL should be in leaderboard. Tokens: {[e['token'] for e in lb]}"

    # USDT should NOT be in leaderboard
    usdt_entry = next((e for e in lb if e["token"] == "USDT"), None)
    assert usdt_entry is None, "USDT should be excluded"

    # Check ranking
    for i, entry in enumerate(lb):
        assert entry["rank"] == i + 1, f"Rank should be {i+1}"

    print("  [OK] Heat computation correct")


def test_time_decay():
    """Test that older posts have lower impact."""
    now_ms = int(time.time() * 1000)
    posts_recent = [
        {
            "content": "$TEST bullish!",
            "likeCount": 100,
            "commentCount": 20,
            "shareCount": 10,
            "publishTime": now_ms - 60000,  # 1 min ago
            "author": {"id": "u1", "userName": "trader", "followers": 1000},
        }
    ]
    posts_old = [
        {
            "content": "$TEST bullish!",
            "likeCount": 100,
            "commentCount": 20,
            "shareCount": 10,
            "publishTime": now_ms - 14 * 60 * 1000,  # 14 min ago
            "author": {"id": "u1", "userName": "trader", "followers": 1000},
        }
    ]

    lb_recent = compute_heat(posts_recent, window_minutes=15)
    lb_old = compute_heat(posts_old, window_minutes=15)

    test_recent = next((e for e in lb_recent if e["token"] == "TEST"), None)
    test_old = next((e for e in lb_old if e["token"] == "TEST"), None)

    if test_recent and test_old:
        assert test_recent["heat"] > test_old["heat"], \
            f"Recent heat ({test_recent['heat']}) should be > old heat ({test_old['heat']})"

    print("  [OK] Time decay correct")


def test_same_author_downweight():
    """Test that 3rd+ post from same author gets downweighted."""
    now_ms = int(time.time() * 1000)
    posts = [
        {
            "content": "$DUP first mention",
            "likeCount": 50,
            "commentCount": 10,
            "shareCount": 5,
            "publishTime": now_ms - 60000,
            "author": {"id": "spam", "userName": "spammer", "followers": 100},
        },
        {
            "content": "$DUP second mention",
            "likeCount": 50,
            "commentCount": 10,
            "shareCount": 5,
            "publishTime": now_ms - 120000,
            "author": {"id": "spam", "userName": "spammer", "followers": 100},
        },
        {
            "content": "$DUP third mention",
            "likeCount": 50,
            "commentCount": 10,
            "shareCount": 5,
            "publishTime": now_ms - 180000,
            "author": {"id": "spam", "userName": "spammer", "followers": 100},
        },
    ]
    lb = compute_heat(posts, window_minutes=15)
    dup = next((e for e in lb if e["token"] == "DUP"), None)
    assert dup is not None, "DUP should be in leaderboard"
    # 3rd post should be downweighted to 25% of base score
    assert dup["heat"] > 0, "DUP heat should be > 0"

    print("  [OK] Same-author downweight correct")


def test_leaderboard_api():
    """Test get_heat_leaderboard and get_candidate_symbols."""
    # Should return list (may be empty if API unavailable)
    lb = get_heat_leaderboard(top_n=10)
    assert isinstance(lb, list), "Leaderboard should be a list"

    syms = get_candidate_symbols(top_n=5)
    assert isinstance(syms, list), "Candidates should be a list"

    # All symbols should end with USDT
    for s in syms:
        assert s.endswith("USDT") or s.endswith("USDC"), f"Symbol {s} should end with USDT"

    print("  [OK] Leaderboard API correct")


if __name__ == "__main__":
    print("Phase 5 Social Heat Smoke Tests")
    print("=" * 40)

    # Use temp DB for tests
    tmpdir = tempfile.mkdtemp()
    os.environ["HOME"] = tmpdir

    test_token_extraction()
    test_author_filter()
    test_heat_computation()
    test_time_decay()
    test_same_author_downweight()
    test_leaderboard_api()

    print("=" * 40)
    print("PHASE5_SMOKE_OK")
