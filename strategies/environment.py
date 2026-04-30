"""
Environment Checker — 综合环境检查
开仓前多维度对齐
"""
try:
    from ..market import Market
    from ..config import ENV_MIN_SCORE
except ImportError:
    from market import Market
    from config import ENV_MIN_SCORE


class EnvironmentCheck:
    """
    返回 (passed: bool, analysis: dict, score: int)
    """

    @staticmethod
    def check(symbol: str, signal: dict) -> tuple:
        analysis = {}
        score = 0

        # 1. BTC环境
        btc_data = Market.ticker("BTCUSDT")
        try:
            btc_chg = float(btc_data.get("priceChangePercent", 0)) if isinstance(btc_data, dict) else 0
        except (TypeError, ValueError):
            btc_chg = 0
        direction = signal["direction"]

        if direction == "long":
            if btc_chg > -2:
                score += 1
                analysis["btc_env"] = f"BTC {btc_chg:+.1f}% 环境正常 +1"
            elif btc_chg < -5:
                score -= 1
                analysis["btc_env"] = f"BTC {btc_chg:+.1f}% 暴跌中做多危险 -1"
            else:
                analysis["btc_env"] = f"BTC {btc_chg:+.1f}% 偏弱 0"
        else:
            if btc_chg < 2:
                score += 1
                analysis["btc_env"] = f"BTC {btc_chg:+.1f}% 环境正常 +1"
            elif btc_chg > 5:
                score -= 1
                analysis["btc_env"] = f"BTC {btc_chg:+.1f}% 暴涨中做空危险 -1"
            else:
                analysis["btc_env"] = f"BTC {btc_chg:+.1f}% 偏强 0"

        # 2. 市场情绪
        fng = Market.fear_greed_index()
        if fng is not None:
            if direction == "long":
                if fng <= 25:
                    score += 1
                    analysis["sentiment"] = f"FGI={fng}极度恐惧 逆向做多 +1"
                elif fng >= 75:
                    score -= 1
                    analysis["sentiment"] = f"FGI={fng}极度贪婪 做多风险 -1"
                else:
                    analysis["sentiment"] = f"FGI={fng}中性 0"
            else:
                if fng >= 75:
                    score += 1
                    analysis["sentiment"] = f"FGI={fng}极度贪婪 逆向做空 +1"
                elif fng <= 25:
                    score -= 1
                    analysis["sentiment"] = f"FGI={fng}极度恐惧 做空风险 -1"
                else:
                    analysis["sentiment"] = f"FGI={fng}中性 0"
        else:
            analysis["sentiment"] = "FGI获取失败 0"

        # 3. OI检查
        try:
            oi = Market.open_interest(symbol)
            ticker_data = Market.ticker(symbol)
            price = float(ticker_data["lastPrice"]) if ticker_data else 0
            oi_usd = oi * price
            if oi_usd > 5_000_000:
                score += 1
                analysis["oi"] = f"OI={oi_usd/1e6:.1f}M 有关注度 +1"
            else:
                analysis["oi"] = f"OI={oi_usd/1e6:.1f}M 关注度低 0"
        except:
            analysis["oi"] = "OI获取失败 0"

        # 4. 成交量
        try:
            ticker_data = ticker_data or Market.ticker(symbol)
            vol = float(ticker_data.get("quoteVolume", 0)) if ticker_data else 0
            if vol > 50_000_000:
                score += 1
                analysis["volume"] = f"24h量={vol/1e6:.0f}M 活跃 +1"
            elif vol > 20_000_000:
                analysis["volume"] = f"24h量={vol/1e6:.0f}M 一般 0"
            else:
                score -= 1
                analysis["volume"] = f"24h量={vol/1e6:.0f}M 冷清 -1"
        except:
            analysis["volume"] = "量能获取失败 0"

        # 5. 信号强度
        if signal["strength"] == "S":
            score += 2
        elif signal["strength"] == "A":
            score += 1

        analysis["verdict"] = f"综合得分:{score}/7"

        passed = score >= ENV_MIN_SCORE
        return passed, analysis, score
