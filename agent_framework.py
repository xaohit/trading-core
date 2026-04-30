"""
Phase 8C/D/E: MAKIMA Agent Framework.
This is the "Brain Loop" for the Agent. It defines the lifecycle of the Agent:
Scan → Decide → Execute → Review (Daily) → Evolve (Weekly).
"""

import time
import json
import logging
from typing import List, Dict, Optional

from agent_tools import (
    get_market_analysis,
    record_agent_decision,
    review_due_decisions,
    get_experience_library,
    store_reflection
)
from db.connection import init_db
from config import DECISION_REVIEW_HORIZON_HOURS

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("MakimaAgent")

class MakimaAgent:
    def __init__(self, name: str = "MAKIMA"):
        self.name = name
        init_db()
        logger.info(f"🤖 Agent {self.name} initialized. Framework loaded.")

    # ==========================================================
    # 1. 核心主循环 (The Heartbeat)
    # ==========================================================
    def run_live(self, interval_seconds: int = 300):
        """
        7x24h 运行主循环。
        这个循环控制了 Agent 的节奏，确保他不会过度交易，也不会忘记复盘。
        """
        logger.info(f"🟢 Starting live loop with interval {interval_seconds}s...")
        
        last_review_time = 0
        last_evolution_time = 0

        while True:
            try:
                current_time = time.time()
                
                # A. 常规扫描 (每 5 分钟)
                self.scan_and_act()

                # B. 每日反思 (每 24 小时触发一次，或者检查到有到期的决策时)
                # 这里简化为每 24 小时强制深度反思一次
                if current_time - last_review_time > 86400: 
                    self.daily_reflection_routine()
                    last_review_time = current_time

                # C. 每周进化 (每 7 天触发一次)
                if current_time - last_evolution_time > 604800:
                    self.weekly_evolution_routine()
                    last_evolution_time = current_time

                logger.info(f"💤 Sleeping for {interval_seconds}s...")
                time.sleep(interval_seconds)

            except KeyboardInterrupt:
                logger.info("🛑 Agent stopped manually.")
                break
            except Exception as e:
                logger.error(f"💥 Error in main loop: {e}")
                time.sleep(60) # 防止崩溃死循环

    # ==========================================================
    # 2. 日常行动 (Scan & Act)
    # ==========================================================
    def scan_and_act(self):
        """
        Agent 的日常工作：
        1. 监控市场。
        2. 带着“历史经验”做决定。
        """
        logger.info("👀 Scanning market for opportunities...")
        
        # 示例：监控几个核心币种
        targets = ["BTCUSDT", "ETHUSDT", "SOLUSDT"] 
        
        for symbol in targets:
            try:
                # 1. 获取分析数据 (包含历史经验)
                data = get_market_analysis(symbol)
                if "error" in data:
                    continue

                # 2. 提取相关经验 (Phase 8D)
                experiences = data.get("relevant_experiences", [])
                market_state = data.get("market_state", {}).get("state", "unknown")
                
                logger.info(f"📊 {symbol} | State: {market_state} | Exp: {len(experiences)} found.")

                # 3. 决策逻辑 (这里留给 Hermes 发挥，框架提供一个默认入口)
                self.make_decision(symbol, data)
                
            except Exception as e:
                logger.error(f"Failed to scan {symbol}: {e}")

    def make_decision(self, symbol: str, market_data: dict):
        """
        决策点。
        Hermes 应该重写或在此处注入逻辑：基于数据判断 open_long, open_short, 还是 wait。
        """
        # 默认逻辑：如果信心度不够，就只是观察。
        # 实际使用时，Hermes 会分析 market_data 并调用 record_agent_decision
        logger.debug(f"🧠 Thinking about {symbol}... (Waiting for Agent Logic)")

    # ==========================================================
    # 3. 自动反思机制 (Phase 8C)
    # ==========================================================
    def daily_reflection_routine(self):
        """
        每天自动运行的“复盘课”。
        检查过去 24h 的决策，找出亏损单，要求 Agent 反思。
        """
        logger.info("🧠 Starting Daily Reflection Routine...")
        
        results = review_due_decisions()
        
        if not results or "failures" not in results:
            logger.info("✅ No decisions due for review today. Good job.")
            return

        failures = results["failures"]
        logger.warning(f"⚠️ Found {len(failures)} failed decisions requiring reflection.")

        for failure in failures:
            self.trigger_reflection(failure)

    def trigger_reflection(self, failed_decision: dict):
        """
        针对单笔失败交易的反思流程。
        这里会调用 Hermes 的 LLM 能力。
        """
        symbol = failed_decision.get("symbol")
        pnl = failed_decision.get("return_pct", 0)
        
        logger.info(f"🧐 Reflecting on {symbol} (PnL: {pnl}%)...")
        
        # 1. 生成反思提示词
        # 这里的 Prompt 会由 Hermes 读取，并给出回答
        prompt = (
            f"你之前在 {symbol} 上做了一个错误决策。 "
            f"当时市场状态是 {failed_decision.get('market_state', {})}。"
            f"结果是亏损了 {pnl}%。"
            f"请反思：1. 哪个信号误导了你？ 2. 下次如何避免？"
        )
        
        # 2. 等待 Hermes 回答 (这一步需要 Hermes 接入)
        # 这里我们假设 Hermes 会调用 store_reflection 来回答
        logger.info(f"📝 Prompt sent to Agent: {prompt}")
        
        # 3. 如果 Agent 自动回复了，我们存入库
        # self.store_reflection(failed_decision['id'], hermes_reply, tags)

    # ==========================================================
    # 4. 规则自我进化 (Phase 8E)
    # ==========================================================
    def weekly_evolution_routine(self):
        """
        每周一次“版本更新”。
        根据经验库，调整系统的运行参数。
        """
        logger.info("🧬 Starting Weekly Evolution Routine...")
        
        experiences = get_experience_library(limit=50)
        if not experiences:
            logger.info("📚 No enough experience to evolve yet.")
            return

        # 统计胜率/败率模式
        losses = [e for e in experiences if e.get("outcome_label") == "direction_wrong"]
        wins = [e for e in experiences if e.get("outcome_label") == "direction_correct"]
        
        logger.info(f"📊 Stats: {len(wins)} Wins, {len(losses)} Losses.")

        # 简单的自动进化逻辑示例：
        # 如果某个模式连续亏损，Hermes 应该在这里调整 Config 或 State
        # 例如：如果发现震荡市亏损多，Hermes 可以自动调高开仓门槛
        
        self.evolve_rules(experiences)

    def evolve_rules(self, experiences: List[dict]):
        """
        实际执行规则修改。
        这个函数是 Hermes "改变系统" 的手。
        """
        logger.info("🔧 Attempting to evolve rules based on recent experiences...")
        
        # Hermes 可以修改 state.json 或 config.py 中的阈值
        # 例如：
        # if loss_pattern_detected:
        #     state.set("min_entry_score", 60) # 提高门槛
        pass

# 如果作为脚本运行，可以测试
if __name__ == "__main__":
    agent = MakimaAgent()
    # 测试一下反思流程
    agent.daily_reflection_routine()
