"""
Scanner — main trading loop.

Phases:
1. 预过滤候选币（社交热度 / 全市场）
2. 多策略信号检测
3. Phase 4: 策略路由（市场状态 → 策略权重）
4. 环境检查 + 决策流水线
5. 风控 + 开仓
6. 持仓监控（TP 金字塔 + 追踪止损）
"""

import json
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

try:
    from .config import (
        MAX_OPEN_POSITIONS,
        COOLDOWN_HOURS, MIN_NOTIONAL_USDT,
        TP1_CLOSE_PCT, TP2_CLOSE_PCT, TP1_R_MULTIPLE, TP2_R_MULTIPLE,
        ATR_STOP_MULTIPLIER, RISK_PER_TRADE_PCT, TRAILING_STOP_ATR_MULT,
    )
    from .market import Market
    from .state import State
    from .db.trades import TradeDB
    from .db.connection import init_db
    from .risk.risk import RiskManager
    from .decision_pipeline import DecisionPipeline
    from .decision_provider import get_decision_provider
    from .execution.executor import Executor
    from .strategies.detectors import detect_all
    from .strategies.environment import EnvironmentCheck
    from .memory.decision_memory import DecisionMemory as Memory
    from .reflection import StrategyWeighter, FailureArchive
    from .market_regime import MarketRegimeDetector, Regime, classify_market_state
    from .strategy_router import StrategyRouter
except ImportError:
    from config import (
        MAX_OPEN_POSITIONS,
        COOLDOWN_HOURS, MIN_NOTIONAL_USDT,
        TP1_CLOSE_PCT, TP2_CLOSE_PCT, TP1_R_MULTIPLE, TP2_R_MULTIPLE,
        ATR_STOP_MULTIPLIER, RISK_PER_TRADE_PCT, TRAILING_STOP_ATR_MULT,
    )
    from market import Market
    from state import State
    from db.trades import TradeDB
    from db.connection import init_db
    from risk.risk import RiskManager
    from decision_pipeline import DecisionPipeline
    from decision_provider import get_decision_provider
    from execution.executor import Executor
    from strategies.detectors import detect_all
    from strategies.environment import EnvironmentCheck
    from memory.decision_memory import DecisionMemory as Memory
    from reflection import StrategyWeighter, FailureArchive
    from market_regime import MarketRegimeDetector, Regime, classify_market_state
    from strategy_router import StrategyRouter


TZ_UTC8 = timezone(timedelta(hours=8))


class Scanner:
    """主扫描器"""

    def __init__(self):
        init_db()
        self.state = State()
        self.risk = RiskManager(self.state)
        self.pipeline = DecisionPipeline(self.risk)
        self.decision_provider = get_decision_provider()
        self.router = StrategyRouter()
        try:
            btc_regime = MarketRegimeDetector.detect("BTCUSDT")
            self.router.set_btc_regime(btc_regime)
        except Exception:
            pass
        self._now = datetime.now(TZ_UTC8)

    # ── 持仓监控 ──────────────────────────────────────────────────────────

    def monitor(self) -> list:
        """监控所有持仓，TP 金字塔 + 追踪止损 / 硬止损"""
        actions = []
        positions = TradeDB.get_open()
        if not positions:
            return actions

        tickers = Market.all_tickers()
        ticker_map = {t["symbol"]: float(t["lastPrice"]) for t in tickers}

        for pos in positions:
            symbol = pos["symbol"]
            price = ticker_map.get(symbol)
            if not price:
                continue

            # 1. 更新追踪止损
            new_trail = Executor.update_trailing_stop(pos, price)
            if new_trail:
                TradeDB.update(pos["id"], trailing_stop=new_trail)
                pos["trailing_stop"] = new_trail

            # 2. 检查 TP / SL
            tp_actions = Executor.check_tp_levels(pos, price)
            if not tp_actions:
                continue

            action = tp_actions[0]
            entry = self._handle_exit_action(action, pos, price, symbol)
            if entry:
                actions.append(entry)

        # 有全平 → 检查是否触发演化
        full_closes = [a for a in actions if not a.get("partial")]
        if full_closes:
            try:
                closed_total = TradeDB.get_closed_count()
                last_evo = self.state.get("last_evolution_count", 0)
                if closed_total - last_evo >= 10:
                    evolved = Memory.evolve_params()
                    if evolved:
                        self.state.set("last_evolution_count", closed_total)
                        self.state.set("last_evolution", int(time.time()))
                        self.state.save()
            except Exception:
                pass

        return actions

    def _handle_exit_action(self, action: dict, pos: dict, price: float, symbol: str) -> Optional[dict]:
        """统一处理 tp1/tp2/trailing/sl 平仓动作"""
        action_type = action["type"]
        pnl_pct = round(action["pnl_pct"], 2)
        pnl_usd = action["pnl_usd"]
        remaining = pos.get("remaining_pct", 100)

        if action_type == "tp1":
            remaining_pct = remaining - TP1_CLOSE_PCT
            reason = f"TP1+{TP1_CLOSE_PCT}%"
            TradeDB.partial_close(
                pos["id"], price, _now_str(),
                f"tp1_{TP1_CLOSE_PCT}%", pnl_pct, pnl_usd,
                TP1_CLOSE_PCT, remaining_pct,
                new_stop=pos["entry_price"],
            )
            partial = True

        elif action_type == "tp2":
            remaining_pct = max(remaining - TP2_CLOSE_PCT, 0)
            reason = f"TP2+{TP2_CLOSE_PCT}%"
            TradeDB.partial_close(
                pos["id"], price, _now_str(),
                f"tp2_{TP2_CLOSE_PCT}%", pnl_pct, pnl_usd,
                TP2_CLOSE_PCT, remaining_pct,
            )
            partial = True

        elif action_type == "trailing":
            reason = "追踪止损"
            Executor.close_position(pos["id"], price, reason, pnl_pct, pnl_usd)
            remaining_pct = 0
            partial = False

        elif action_type == "sl":
            reason = "止损"
            Executor.close_position(pos["id"], price, reason, pnl_pct, pnl_usd)
            remaining_pct = 0
            partial = False

        else:
            return None

        # 统一复盘记录
        sig_type = (
            json.loads(pos["pre_analysis"]).get("type", "")
            if isinstance(pos.get("pre_analysis"), str) and pos.get("pre_analysis")
            else (pos.get("pre_analysis", {}) or {}).get("type", "")
        )
        Memory.record_outcome(pos["id"], symbol, sig_type, pos["direction"], pnl_pct, pnl_usd, reason)
        self.state.record_trade(pnl_pct, pnl_usd)

        if not partial and pnl_pct < 0:
            try:
                FailureArchive.archive(pos["id"], [], exit_reason=reason)
            except Exception:
                pass

        entry = {"exit_price": price, "pnl_usd": pnl_usd, "reason": reason, "partial": partial}
        if not partial:
            entry.update(pos)
        else:
            entry["remaining_pct"] = remaining_pct
        return entry

    # ── 完整扫描 ──────────────────────────────────────────────────────────

    def scan(self) -> dict:
        """
        完整扫描：
        1. 预过滤候选币
        2. 刷新 router（BTC regime + 权重）
        3. 多策略检测 + 策略路由
        4. 环境检查 + 决策流水线
        5. 风控 + 开仓
        """
        # 持仓检查
        open_positions = TradeDB.get_open()
        open_symbols = {p["symbol"] for p in open_positions}
        if len(open_positions) >= MAX_OPEN_POSITIONS:
            return {"action": "max_positions", "positions": len(open_positions), "opened": 0}
            return {"action": "max_positions", "positions": len(open_positions), "opened": 0}

        # 刷新 router（BTC regime + 最新权重）
        try:
            btc_regime = MarketRegimeDetector.detect("BTCUSDT")
            self.router.set_btc_regime(btc_regime)
        except Exception:
            pass

        # 候选币
        tickers = Market.all_tickers()
        if not tickers:
            return {"action": "no_tickers", "opened": 0}

        funding_rates = Market.funding_rates()
        ticker_map = {t["symbol"]: t for t in tickers}
        heat_used = False
        heat_candidates = []

        # 社交热度预过滤
        try:
            from .social_heat import get_candidate_symbols as get_heat_candidates
        except ImportError:
            from social_heat import get_candidate_symbols as get_heat_candidates
        try:
            heat_raw = get_heat_candidates()
            if heat_raw:
                heat_candidates = [s for s in heat_raw if s in ticker_map]
                if len(heat_candidates) >= 3:
                    heat_used = True
        except Exception:
            pass

        if heat_used:
            candidates = [ticker_map[s] for s in heat_candidates]
        else:
            candidates = self.risk.filter_candidates(tickers)

        all_signals = []

        for ticker in candidates:
            symbol = ticker["symbol"]
            if symbol in open_symbols:
                continue
            if self.state.is_cooling(symbol, hours=COOLDOWN_HOURS):
                continue

            ticker["__klines_1h_24"] = Market.klines(symbol, "1h", 24)
            ticker["__klines_1h_6"] = Market.klines(symbol, "1h", 6)

            signals = detect_all(symbol, ticker, funding_rates)
            if not signals:
                continue

            # Phase 4: 策略路由
            filtered = self.router.route(signals)
            if not filtered:
                continue

            best = filtered[0]

            passed, analysis, score = EnvironmentCheck.check(symbol, best)
            best["env_score"] = score
            best["env_analysis"] = analysis
            snapshot, signal_analysis = self._score_market(symbol)
            composite_score = signal_analysis.get("score", 0)
            best["snapshot"] = snapshot
            best["analysis"] = signal_analysis
            best["composite_score"] = composite_score
            best["verdict"] = signal_analysis.get("verdict")
            best["tags"] = signal_analysis.get("tags", [])
            best["experience_context"] = Memory.retrieve_for_signal(
                symbol, best, signal_analysis, limit=3
            )

            pipeline_decision = self.pipeline.evaluate(
                symbol=symbol, signal=best, snapshot=snapshot,
                analysis=signal_analysis, env_passed=passed,
                env_analysis=analysis, env_score=score,
            )
            best["pipeline_decision"] = pipeline_decision.__dict__
            if not pipeline_decision.ok:
                self._record_reject(symbol, best, snapshot, signal_analysis, pipeline_decision)
                continue

            all_signals.append(best)

        if not all_signals:
            return {
                "action": "none", "signals_found": 0,
                "opened": 0, "heat_used": heat_used,
                "heat_candidates": len(heat_candidates) if heat_used else 0,
            }

        strategy_weights = StrategyWeighter.get_weights()
        all_signals.sort(key=lambda x: (
            {"S": 0, "A": 1, "B": 2}.get(x.get("strength"), 3),
            -x.get("composite_score", 0),
            -x.get("env_score", 0),
            -strategy_weights.get(x.get("type", ""), 0.25),
            -x.get("_router_weight", 1.0),
        ))

        best_signal = all_signals[0]

        if best_signal["strength"] == "B":
            return {"action": "skip_b", "best": best_signal, "opened": 0}

        agent_decision = self._agent_gate(best_signal)
        best_signal["agent_decision"] = agent_decision
        if not agent_decision.get("approved"):
            TradeDB.record_signal(
                _now_str(),
                best_signal["symbol"],
                best_signal,
                best_signal.get("composite_score", best_signal.get("env_score", 0)),
                "agent_reject",
                agent_decision.get("reasoning"),
                best_signal.get("snapshot", {}),
                best_signal.get("analysis", {}),
            )
            self._remember_decision(
                best_signal["symbol"], "agent_reject", best_signal,
                best_signal.get("snapshot", {}),
                best_signal.get("analysis", {}),
                agent_decision.get("reasoning"),
                horizon_hours=1,
            )
            return {
                "action": "agent_reject",
                "best": best_signal,
                "agent_decision": agent_decision,
                "opened": 0,
                "heat_used": heat_used,
            }

        # 开仓
        trade = Executor.open_position(
            best_signal["symbol"],
            best_signal["direction"],
            best_signal,
        )

        if not trade:
            Executor.log(f"⚠️  开仓失败: {best_signal['symbol']} — 无有效价格")
            self._record_reject(
                best_signal['symbol'], best_signal,
                best_signal.get('snapshot', {}),
                best_signal.get('analysis', {}),
                type('_D', (), {'score': 0, 'action': 'open_failed',
                 'reason': 'price unavailable'})(),
            )
            return {
                "action": "open_failed", "reason": "price unavailable",
                "signal": best_signal, "opened": 0, "heat_used": heat_used,
            }

        TradeDB.record_signal(
            _now_str(),
            best_signal["symbol"],
            best_signal,
            best_signal.get("composite_score", best_signal.get("env_score", 0)),
            "opened",
            best_signal.get("verdict"),
            best_signal.get("snapshot", {}),
            best_signal.get("analysis", {}),
        )
        self._remember_decision(
            best_signal["symbol"], "opened", best_signal,
            best_signal.get("snapshot", {}),
            best_signal.get("analysis", {}),
            best_signal.get("verdict"),
            trade,
        )

        return {
            "action": "opened",
            "trade": trade,
            "signal": best_signal,
            "opened": 1,
            "heat_used": heat_used,
        }

    # ── 辅助方法 ──────────────────────────────────────────────────────────

    def _score_market(self, symbol: str) -> tuple:
        """技术分析评分 + 市场快照"""
        try:
            from .market_snapshot import market_snapshot
        except ImportError:
            from market_snapshot import market_snapshot
        try:
            snapshot = market_snapshot(symbol)
        except Exception:
            snapshot = {}
        signal_analysis = {}
        try:
            from .monitor.signal_engine import scan_symbol
        except ImportError:
            from monitor.signal_engine import scan_symbol
        try:
            signal_analysis = scan_symbol(symbol)
        except Exception:
            pass
        return snapshot, signal_analysis

    def _agent_gate(self, signal: dict) -> dict:
        """LLM 决策门"""
        try:
            return self.decision_provider.decide(signal)
        except Exception:
            return {"approved": True, "reasoning": "LLM unavailable — auto approve"}

    def _record_reject(self, symbol: str, signal: dict, snapshot: dict, analysis: dict, decision):
        TradeDB.record_signal(
            _now_str(),
            symbol,
            signal,
            signal.get("composite_score", signal.get("env_score", 0)),
            decision.action if hasattr(decision, 'action') else "reject",
            decision.reason if hasattr(decision, 'reason') else "",
            snapshot,
            analysis,
        )
        reason = decision.reason if hasattr(decision, 'reason') else ""
        Memory.record_snapshot(
            symbol, "reject", signal, snapshot, analysis, reason=reason
        )

    def _remember_decision(self, symbol: str, action: str, signal: dict,
                           snapshot: dict, analysis: dict, reason: str = "",
                           trade: dict = None, horizon_hours: int = 24):
        """记录决策到 memory / decision_snapshots"""
        try:
            market_state = classify_market_state(symbol)
        except Exception:
            market_state = {}
        Memory.record_snapshot(
            symbol, action, signal, snapshot, analysis,
            reason=reason, trade=trade, market_state=market_state,
            horizon_hours=horizon_hours,
        )

    # ── 主循环 ────────────────────────────────────────────────────────────

    def run(self) -> dict:
        """完整运行：监控 + 扫描，返回 main.py 期望的格式"""
        ts = _now_str()
        closed = self.monitor()
        scan_result = self.scan()
        return {
            "timestamp": ts,
            "closed": closed,
            "scan": scan_result,
        }


# ── 独立运行入口 ────────────────────────────────────────────────────────

def _now_str():
    return datetime.now(TZ_UTC8).strftime("%m-%d %H:%M")


def main():
    scanner = Scanner()
    result = scanner.run()
    action = result.get("action", "?")
    opened = result.get("opened", 0)
    closed = len(result.get("monitor_actions", []))
    heat_used = "🌡" if result.get("heat_used") else "full"
    print(f"[{_now_str()}] {heat_used} | closed={closed} opened={opened}")
    return result


if __name__ == "__main__":
    main()
