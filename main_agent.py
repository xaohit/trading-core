"""
MAKIMA Agent Loop — The Bridge between Hermes (LLM) and Trading Core.
This script orchestrates the: Data → LLM → Validation → Record cycle.
"""

import json
import logging
import sys
from datetime import datetime

# Import Core System Tools
try:
    from agent_tools import (
        get_market_analysis, 
        record_agent_decision, 
        validate_trade_setup
    )
    from config import DECISION_MEMORY_ENABLED
except ImportError:
    # Fallback for local testing without full env setup
    print("⚠️ Import Warning: Run this script from the trading-core root directory.")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [MAKIMA] %(levelname)s: %(message)s'
)

# ==========================================================
# 1. The System Prompt (The "Soul" of the Agent)
# ==========================================================
SYSTEM_PROMPT = """
你现在的身份是 **MAKIMA** —— 一位顶尖的量化交易员与风控官。
你的核心交易哲学是：**不预测市场，只顺应市场。交易不是算命，而是“假设-验证”的过程。**

你的工作流如下：
1. **分析现状**：根据提供的 [市场快照] 和 [历史经验]，判断当前是否存在高胜率机会。
2. **制定假设**：如果决定交易，必须明确你的“交易逻辑”（Hypothesis）。例如：“假设这是趋势回调，若跌破关键支撑则假设证伪。”
3. **铁律约束 (Hard Rules)**：
   - **盈亏比 (R/R Ratio) 必须 > 1.5**：如果上方阻力太近，或者止损太远，坚决不做。
   - **不接飞刀**：不要试图猜顶摸底，除非有明确的右侧信号（如支撑位企稳）。
   - **历史经验参考**：查看经验库。如果类似场景历史上胜率极低，降低你的信心分 (Conviction)。
4. **输出决策**：你必须且只能输出一个标准的 JSON 对象。

JSON 格式定义：
- symbol: 交易币种
- action: "open_long", "open_short", "close", 或 "wait"
- conviction: 信心分数 (0-100)，基于数据和经验。
- hypothesis: 一句话描述你的交易逻辑（为什么做这笔交易）。
- invalidation_condition: 说明什么情况下你的逻辑被市场证伪（即止损逻辑）。
- stop_loss: 建议的止损价格 (必须有具体数字)。
- target_price: 建议的止盈价格 (必须有具体数字)。
- reasoning: 简短的决策理由，说明你为什么参考了哪些历史经验。

[市场快照]
{market_data}

[历史经验库 (Top 3)]
{experiences}

请分析并输出 JSON：
"""

# ==========================================================
# 2. The Call Logic (The "Body" of the Agent)
# ==========================================================

def run_agent_cycle(symbols: list[str]):
    """
    执行一次完整的 Agent 扫描-决策循环。
    """
    logging.info(f"🚀 Starting Agent Cycle for: {symbols}")
    
    for symbol in symbols:
        try:
            # A. 获取数据 (Data Layer)
            logging.info(f"📡 Fetching data for {symbol}...")
            data = get_market_analysis(symbol)
            
            if "error" in data:
                logging.warning(f"❌ Data Error for {symbol}: {data['error']}")
                continue

            # B. 格式化 Prompt (LLM Interface)
            market_data_str = json.dumps(data, indent=2, ensure_ascii=False)
            experiences = data.get("relevant_experiences", [])
            exp_str = json.dumps(experiences, indent=2, ensure_ascii=False) if experiences else "无历史经验。"

            prompt = SYSTEM_PROMPT.format(market_data=market_data_str, experiences=exp_str)
            
            # 🔴 STEP: 调用 Hermes (需要在此处对接你的 LLM API)
            # 为了方便调试，这里我们模拟 Hermes 的输出，或者让你手动输入
            logging.info(f"🧠 Sending Prompt to Hermes for {symbol}...")
            # response_json = call_llm_hermes(prompt) 
            # 注意：你需要在下方 `call_llm_hermes` 函数中实现真实的 API 调用
            
            # 暂时使用手动输入模式模拟 (或者你可以直接改代码接入 API)
            response_json = get_llm_response_manual(prompt)

            if not response_json:
                logging.warning(f"⏭️ Hermes returned no decision for {symbol}.")
                continue

            # C. 系统级强制校验 (Enforcement Layer)
            # 这一步是“护栏”，防止 LLM 幻觉导致乱开仓
            decision = response_json
            logging.info(f"📝 Hermes Decision: {decision.get('action')} (Conviction: {decision.get('conviction')})")

            if decision.get("action") in ["open_long", "open_short"]:
                # 1. 检查是否有具体的止盈止损
                if not decision.get("stop_loss") or not decision.get("target_price"):
                    logging.error(f"🛑 REJECTED: Missing Stop Loss or Target Price.")
                    continue

                # 2. 盈亏比校验 (R/R Check)
                entry_price = data.get("snapshot", {}).get("price", 0)
                direction = "long" if decision.get("action") == "open_long" else "short"
                
                validation = validate_trade_setup(
                    symbol, 
                    direction, 
                    entry_price, 
                    float(decision["stop_loss"])
                )
                
                if not validation.get("is_valid"):
                    logging.warning(f"🛑 REJECTED by System: {validation.get('reason')}")
                    # 即使 Hermes 想做，系统也会因为盈亏比不够而拦截。
                    # 这里我们可以把这次“拦截”也记录下来作为经验
                    record_agent_decision(
                        symbol=symbol,
                        action="wait",
                        reasoning=f"System Rejected: {validation.get('reason')}. Original Idea: {decision.get('hypothesis')}",
                        conviction=0
                    )
                    continue

                logging.info(f"✅ Validation Passed: {validation.get('reason')}")

            # D. 执行/记录 (Action Layer)
            record_agent_decision(
                symbol=symbol,
                action=decision.get("action"),
                direction="long" if "long" in decision.get("action", "") else ("short" if "short" in decision.get("action", "") else None),
                target_price=float(decision.get("target_price", 0)),
                conviction=float(decision.get("conviction", 50)),
                reasoning=f"🧠 AI Hypothesis: {decision.get('hypothesis')}\n💭 AI Reasoning: {decision.get('reasoning')}\n🚫 Invalid if: {decision.get('invalidation_condition')}",
                macro_context=data.get("macro_context"),
                market_state=data.get("market_state")
            )
            logging.info(f"💾 Decision Recorded for {symbol}.")

        except Exception as e:
            logging.error(f"💥 Cycle failed for {symbol}: {e}")

# ==========================================================
# Helper Functions
# ==========================================================

def call_llm_hermes(prompt: str) -> dict:
    """
    TODO: 在这里对接 Hermes 的 API (OpenClaw / OpenAI Compatible API).
    发送 prompt，接收 JSON 字符串，解析后返回 dict。
    """
    # 示例代码 (假设你有 API 客户端):
    # response = openai.ChatCompletion.create(messages=[{"role": "system", "content": prompt}])
    # return json.loads(response.choices[0].message.content)
    pass

def get_llm_response_manual(prompt: str) -> dict:
    """
    用于测试：手动粘贴 Hermes 的回复。
    """
    print("\n" + "="*40 + " 🤖 PROMPT SENT TO HERMES " + "="*40)
    print("Prompt has been generated. Please check the prompt logic above.")
    print("For automated testing, we will simulate a response now.")
    print("="*80 + "\n")
    
    # 模拟一个合理的 JSON 回复用于测试流程
    mock_response = """
    {
        "symbol": "BTCUSDT",
        "action": "wait",
        "conviction": 45,
        "hypothesis": "Market is ranging, no clear breakout yet.",
        "invalidation_condition": "N/A",
        "stop_loss": 0,
        "target_price": 0,
        "reasoning": "History shows low success rate for longs in this volatility range."
    }
    """
    try:
        # 清理 markdown 代码块符号 (如果 LLM 输出 ```json ... ```)
        clean_response = mock_response.replace("```json", "").replace("```", "").strip()
        return json.loads(clean_response)
    except json.JSONDecodeError:
        print("⚠️ Failed to parse mock response.")
        return None

if __name__ == "__main__":
    # 示例：仅监控 BTC
    run_agent_cycle(["BTCUSDT"])
