"""
Scanner — 币种扫描 + 信号检测 + 开仓决策
"""
from datetime import datetime, timezone, timedelta

try:
    from .config import (
        MAX_OPEN_POSITIONS, EXCLUDE_SYMBOLS, MIN_VOLUME_M, COOLDOWN_HOURS,
        TP1_CLOSE_PCT, TP2_CLOSE_PCT, SOCIAL_HEAT_ENABLED, HEAT_CANDIDATE_N,
        ENTRY_QUALITY_MIN_PASSED, ENTRY_QUALITY_MIN_SCORE,
    )
    from .market import Market
    from .state import State
    from .db.trades import TradeDB
    from .db.connection import init_db
    from .strategies.detectors import detect_all
    from .strategies.environment import EnvironmentCheck
    from .risk import RiskManager
    from .executor import Executor
    from .memory import Memory
    from .market_snapshot import get_market_snapshot
    from .signals import analyze
    from .decision_memory import DecisionMemory
    from .social_heat import get_candidate_symbols as get_heat_candidates
    from .reflection import StrategyWeighter, FailureArchive
    from .market_state import classify_market_state
    from .agent_decision import AgentDecisionGate
    from .ta_checker import assess_trade_setup
except ImportError:
    from config import (
        MAX_OPEN_POSITIONS, EXCLUDE_SYMBOLS, MIN_VOLUME_M, COOLDOWN_HOURS,
        TP1_CLOSE_PCT, TP2_CLOSE_PCT, SOCIAL_HEAT_ENABLED, HEAT_CANDIDATE_N,
        ENTRY_QUALITY_MIN_PASSED, ENTRY_QUALITY_MIN_SCORE,
    )
    from market import Market
    from state import State
    from db.trades import TradeDB
    from db.connection import init_db
    from strategies.detectors import detect_all
    from strategies.environment import EnvironmentCheck
    from risk import RiskManager
    from executor import Executor
    from memory import Memory
    from market_snapshot import get_market_snapshot
    from signals import analyze
    from decision_memory import DecisionMemory
    from social_heat import get_candidate_symbols as get_heat_candidates
    from reflection import StrategyWeighter, FailureArchive
    from market_state import classify_market_state
    from agent_decision import AgentDecisionGate
    from ta_checker import assess_trade_setup


TZ_UTC8 = timezone(timedelta(hours=8))


class Scanner:
    """
    主扫描器：
    - scan(): 完整扫描流程
    - monitor(): 持仓监控（止损止盈）
    - run(): 完整运行（监控 + 扫描）
    """

    def __init__(self):
        init_db()
        self.state = State()
        self.risk = RiskManager(self.state)
        self._now = datetime.now(TZ_UTC8)

    def monitor(self) -> list:
        """
        监控所有持仓，Phase 4B TP 金字塔：
        - 检查 TP1 → 部分平仓 TP1_CLOSE_PCT%，止损移至成本价
        - 检查 TP2 → 部分平仓 TP2_CLOSE_PCT%，剩余仓位走追踪止损
        - 检查追踪止损 → 平剩余全部
        - 检查硬止损 → 平剩余全部
        返回动作列表（部分平仓 + 全平）
        """
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

            # 1. Update trailing stop if in profit
            new_trail = Executor.update_trailing_stop(pos, price)
            if new_trail:
                TradeDB.update(pos["id"], trailing_stop=new_trail)
                pos["trailing_stop"] = new_trail

            # 2. Check TP levels / SL
            tp_actions = Executor.check_tp_levels(pos, price)
            if not tp_actions:
                continue

            action = tp_actions[0]
            action_type = action["type"]

            if action_type == "tp1":
                # Partial close TP1
                remaining = pos.get("remaining_pct", 100)
                close_amount = pos.get("position_usd", 10) * (TP1_CLOSE_PCT / 100)
                pnl_usd = action["pnl_usd"]
                pnl_pct = action["pnl_pct"]
                remaining_pct = remaining - TP1_CLOSE_PCT

                # Move SL to breakeven
                entry = pos["entry_price"]

                TradeDB.partial_close(
                    pos["id"], price, _now_str(),
                    f"tp1_{TP1_CLOSE_PCT}%", pnl_pct, pnl_usd,
                    TP1_CLOSE_PCT, remaining_pct,
                    new_stop=entry  # breakeven
                )

                Memory.record_outcome(
                    pos["id"], symbol,
                    pos.get("pre_analysis", {}).get("type", ""),
                    pos["direction"], pnl_pct, pnl_usd, f"TP1+{TP1_CLOSE_PCT}%"
                )
                actions.append({
                    **pos, "exit_price": price, "pnl_usd": pnl_usd,
                    "reason": f"TP1+{TP1_CLOSE_PCT}%", "partial": True,
                    "remaining_pct": remaining_pct,
                })

            elif action_type == "tp2":
                # Partial close TP2
                remaining = pos.get("remaining_pct", 100)
                close_pct_of_remaining = TP2_CLOSE_PCT
                remaining_pct = max(remaining - close_pct_of_remaining, 0)
                close_amount = pos.get("position_usd", 10) * (close_pct_of_remaining / 100)
                pnl_usd = action["pnl_usd"]
                pnl_pct = action["pnl_pct"]

                TradeDB.partial_close(
                    pos["id"], price, _now_str(),
                    f"tp2_{close_pct_of_remaining}%", pnl_pct, pnl_usd,
                    close_pct_of_remaining, remaining_pct
                )

                Memory.record_outcome(
                    pos["id"], symbol,
                    pos.get("pre_analysis", {}).get("type", ""),
                    pos["direction"], pnl_pct, pnl_usd, f"TP2+{close_pct_of_remaining}%"
                )
                actions.append({
                    **pos, "exit_price": price, "pnl_usd": pnl_usd,
                    "reason": f"TP2+{close_pct_of_remaining}%", "partial": True,
                    "remaining_pct": remaining_pct,
                })

            elif action_type in ("trailing", "sl"):
                # Full close remaining
                remaining = pos.get("remaining_pct", 100)
                pnl_usd = action["pnl_usd"]
                pnl_pct = action["pnl_pct"]
                reason = "追踪止损" if action_type == "trailing" else "止损"

                Executor.close_position(
                    pos["id"], price, reason,
                    round(pnl_pct, 2), pnl_usd
                )
                Memory.record_outcome(
                    pos["id"], symbol,
                    pos.get("pre_analysis", {}).get("type", ""),
                    pos["direction"], pnl_pct, pnl_usd, reason
                )

                # Archive failure with tags (Phase 6)
                if pnl_pct < 0:
                    try:
                        FailureArchive.archive(pos["id"], [], exit_reason=reason)
                    except Exception:
                        pass  # Archive failure shouldn't block trading

                actions.append({
                    **pos, "exit_price": price, "pnl_usd": pnl_usd,
                    "reason": reason, "partial": False,
                })

        # 有平仓 → 检查是否该触发演化
        full_closes = [a for a in actions if not a.get("partial")]
        if full_closes:
            try:
                closed_total = TradeDB.get_closed_count()
                last_evo = self.state.get("last_evolution_count", 0)
                if closed_total - last_evo >= 10:
                    evolved = Memory.evolve_params()
                    if evolved:
                        self.state.set("last_evolution_count", closed_total)
                        self.state.set("last_evolution", int(__import__("time").time()))
                        self.state.save()
            except Exception:
                pass  # 演化失败不影响交易

        return actions

    def scan(self) -> dict:
        """
        完整扫描：
        1. 预过滤候选币（优先用社交热度，否则全市场）
        2. 多策略检测
        3. 环境检查
        4. 风控检查
        5. 开仓
        返回结果摘要
        """
        # 持仓检查
        open_positions = TradeDB.get_open()
        open_symbols = {t["symbol"] for t in open_positions}
        slots = MAX_OPEN_POSITIONS - len(open_positions)

        if slots <= 0:
            return {"action": "full", "opened": 0}

        # Market data
        tickers = Market.all_tickers()
        ticker_map = {t["symbol"]: t for t in tickers}
        funding_rates = Market.funding_rates()

        # Candidate pool: heat → fallback to all-market
        heat_candidates = set()
        heat_used = False
        if SOCIAL_HEAT_ENABLED:
            try:
                heat_syms = get_heat_candidates(top_n=HEAT_CANDIDATE_N)
                heat_candidates = {s for s in heat_syms if s in ticker_map}
                if heat_candidates:
                    heat_used = True
            except Exception:
                pass  # Fall back to all-market

        if heat_used:
            candidates = [ticker_map[s] for s in heat_candidates]
        else:
            candidates = self.risk.filter_candidates(tickers)

        all_signals = []

        for ticker in candidates:
            symbol = ticker["symbol"]

            # 跳过已持仓
            if symbol in open_symbols:
                continue

            # 冷却检查
            if self.state.is_cooling(symbol, hours=COOLDOWN_HOURS):
                continue

            # 检测信号
            signals = detect_all(symbol, ticker, funding_rates)
            if not signals:
                continue

            best = signals[0]  # 已按强度排序

            # 环境检查
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
            best["experience_context"] = DecisionMemory.retrieve_for_signal(
                symbol, best, signal_analysis, limit=3
            )

            if not passed:
                TradeDB.record_signal(
                    _now_str(), symbol, best, score, "env_reject",
                    analysis.get("verdict"), snapshot, signal_analysis
                )
                self._remember_decision(
                    symbol, "env_reject", best, snapshot,
                    signal_analysis, analysis.get("verdict")
                )
                continue

            if self._reject_scored_signal(signal_analysis):
                TradeDB.record_signal(
                    _now_str(), symbol, best, composite_score, "score_reject",
                    signal_analysis.get("verdict"), snapshot, signal_analysis
                )
                self._remember_decision(
                    symbol, "score_reject", best, snapshot,
                    signal_analysis, signal_analysis.get("verdict")
                )
                continue

            # Entry quality gate (Phase 7A): hard vetoes + 7-item checklist
            veto_reason = self._entry_quality_veto(signal_analysis, snapshot)
            if veto_reason:
                TradeDB.record_signal(
                    _now_str(), symbol, best, composite_score, "entry_veto",
                    signal_analysis.get("verdict"), snapshot, signal_analysis
                )
                self._remember_decision(
                    symbol, "entry_veto", best, snapshot,
                    signal_analysis, veto_reason
                )
                continue

            # Entry quality score
            quality, passed_count, quality_notes = self.risk.evaluate_entry_quality(
                symbol, best, signal_analysis
            )
            best["entry_quality"] = quality
            best["entry_quality_notes"] = quality_notes

            if passed_count < ENTRY_QUALITY_MIN_PASSED or composite_score < ENTRY_QUALITY_MIN_SCORE:
                TradeDB.record_signal(
                    _now_str(), symbol, best, composite_score, "quality_reject",
                    signal_analysis.get("verdict"), snapshot, signal_analysis
                )
                self._remember_decision(
                    symbol, "quality_reject", best, snapshot,
                    signal_analysis, f"quality={quality}, passed={passed_count}"
                )
                continue

            # 风控检查
            allowed, risk_reason = self.risk.check_account_risk(symbol)
            if not allowed:
                TradeDB.record_signal(
                    _now_str(), symbol, best, composite_score, "risk_reject",
                    risk_reason, snapshot, signal_analysis
                )
                self._remember_decision(
                    symbol, "risk_reject", best, snapshot,
                    signal_analysis, risk_reason
                )
                continue

            all_signals.append(best)

        if not all_signals:
            return {
                "action": "none", "signals_found": len(all_signals),
                "opened": 0, "heat_used": heat_used,
                "heat_candidates": len(heat_candidates) if heat_used else 0,
            }

        # Get adaptive strategy weights
        strategy_weights = StrategyWeighter.get_weights()

        # 按强度 + 环境得分 + 策略权重排序
        all_signals.sort(key=lambda x: (
            {"S": 0, "A": 1, "B": 2}.get(x["strength"], 3),
            -x.get("composite_score", 0),
            -x.get("env_score", 0),
            -strategy_weights.get(x.get("type", ""), 0.25),  # New: strategy weight
        ))

        best_signal = all_signals[0]

        # B级信号跳过
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

    def _agent_gate(self, signal: dict) -> dict:
        symbol = signal["symbol"]
        direction = signal.get("direction")
        snapshot = signal.get("snapshot", {})
        analysis = signal.get("analysis", {})
        experiences = signal.get("experience_context", [])

        decision = AgentDecisionGate.evaluate(
            symbol=symbol,
            signal=signal,
            snapshot=snapshot,
            analysis=analysis,
            experiences=experiences,
        )
        if not decision.get("approved"):
            return decision

        entry_price = signal.get("price") or snapshot.get("price")
        stop_loss = self._planned_stop_loss(signal, entry_price)
        decision["entry_price"] = entry_price
        decision["stop_loss"] = stop_loss

        validation = self._validate_agent_trade(symbol, direction, entry_price, stop_loss)
        decision["ta_validation"] = validation
        if not validation.get("is_valid"):
            decision["approved"] = False
            decision["action"] = "wait"
            decision["reasoning"] = (
                f"{decision.get('reasoning')} | ta_reject={validation.get('reason')}"
            )
            return decision

        decision["target_price"] = validation.get("target_price")
        decision["r_r_ratio"] = validation.get("r_r_ratio")
        return decision

    @staticmethod
    def _planned_stop_loss(signal: dict, entry_price: float | None) -> float | None:
        if not entry_price:
            return None
        sl_pct = float(signal.get("sl_pct") or 0.05)
        if signal.get("direction") == "long":
            return round(entry_price * (1 - sl_pct), 8)
        if signal.get("direction") == "short":
            return round(entry_price * (1 + sl_pct), 8)
        return None

    @staticmethod
    def _validate_agent_trade(
        symbol: str,
        direction: str | None,
        entry_price: float | None,
        stop_loss: float | None,
    ) -> dict:
        if direction not in {"long", "short"} or not entry_price or not stop_loss:
            return {"is_valid": False, "reason": "missing direction, entry, or stop loss", "r_r_ratio": 0}

        klines = Market.klines(symbol, "1h", limit=50)
        if not klines:
            return {"is_valid": False, "reason": "failed to fetch klines", "r_r_ratio": 0}

        normalized = [
            {"high": row[2], "low": row[3]}
            for row in klines
            if isinstance(row, (list, tuple)) and len(row) >= 4
        ]
        return assess_trade_setup(symbol, direction, entry_price, stop_loss, normalized)

    def _score_market(self, symbol: str) -> tuple[dict, dict]:
        try:
            snapshot = get_market_snapshot(symbol)
            signal_analysis = analyze(snapshot)
            return snapshot, signal_analysis
        except Exception as exc:
            return (
                {"symbol": symbol, "error": str(exc)},
                {
                    "score": 0,
                    "verdict": "snapshot_error",
                    "tags": ["snapshot_error"],
                    "notes": [str(exc)],
                    "oi_divergence": {
                        "type": "unknown",
                        "level": "unknown",
                        "note": str(exc),
                    },
                },
            )

    @staticmethod
    def _reject_scored_signal(signal_analysis: dict) -> bool:
        score = float(signal_analysis.get("score") or 0)
        tags = set(signal_analysis.get("tags") or [])
        hard_tags = {
            "no_price",
            "snapshot_error",
            "price_overheated",
            "funding_hot",
            "long_crowded",
        }
        return score < 43 or bool(tags & hard_tags)

    @staticmethod
    def _entry_quality_veto(signal_analysis: dict, snapshot: dict) -> str | None:
        """
        Phase 7A: Entry quality hard vetoes.
        Returns veto reason string if any veto is hit, None otherwise.

        Hard vetoes (any one → SKIP):
        - verdict is "过热预警"
        - 4h change > 25%
        - 24h change > 50%
        - funding ≥ 0.05%
        - retail LSR ≥ 1.7
        - taker ratio ≥ 1.8
        - taker trend ≤ -5%
        """
        verdict = signal_analysis.get("verdict", "")
        if "过热" in verdict:
            return f"verdict={verdict}"

        change_4h = snapshot.get("change_4h", 0) or 0
        if abs(change_4h) > 25:
            return f"4h change={change_4h}% > 25%"

        change_24h = snapshot.get("change_24h", 0) or 0
        if abs(change_24h) > 50:
            return f"24h change={change_24h}% > 50%"

        funding = snapshot.get("funding_rate", 0) or 0
        if abs(funding) >= 0.05:
            return f"funding={funding}% >= 0.05%"

        global_lsr = snapshot.get("global_lsr", 1.0) or 1.0
        if global_lsr >= 1.7:
            return f"retail LSR={global_lsr} >= 1.7"

        taker_ratio = snapshot.get("taker_ratio", 1.0) or 1.0
        if taker_ratio >= 1.8:
            return f"taker ratio={taker_ratio} >= 1.8"

        taker_trend = snapshot.get("taker_trend_pct", 0) or 0
        if taker_trend <= -5:
            return f"taker trend={taker_trend}% <= -5%"

        return None

    @staticmethod
    def _remember_decision(
        symbol: str,
        action: str,
        signal: dict,
        snapshot: dict,
        signal_analysis: dict,
        result: str | None = None,
        trade: dict | None = None,
    ):
        try:
            # Phase 8A: Capture Market State and Macro Context
            market_state = classify_market_state(symbol)
            
            # Macro Context
            btc_data = Market.ticker("BTCUSDT")
            btc_chg = float(btc_data.get("priceChangePercent", 0)) if isinstance(btc_data, dict) else 0
            fng = Market.fear_greed_index()
            
            macro_context = {
                "btc_24h_change": btc_chg,
                "fear_greed_index": fng,
            }
            
            DecisionMemory.record_decision(
                symbol=symbol,
                action=action,
                signal=signal,
                snapshot=snapshot,
                analysis=signal_analysis,
                result=result,
                source_trade_id=(trade or {}).get("id"),
                experiences=signal.get("experience_context", []),
                macro_context=macro_context,
                market_state=market_state,
                agent_reasoning=(signal.get("agent_decision") or {}).get("reasoning"),
            )
        except Exception:
            pass

    def run(self) -> dict:
        """
        完整运行：监控 → 扫描
        """
        closed = self.monitor()
        scan_result = self.scan()

        # 更新扫描状态
        self.state.set("last_scan", _now_str())
        self.state.set("scan_count", self.state.get("scan_count", 0) + 1)
        self.state.save()

        return {
            "closed": closed,
            "scan": scan_result,
            "timestamp": _now_str(),
        }


def _now_str():
    return datetime.now(TZ_UTC8).strftime("%m-%d %H:%M")
