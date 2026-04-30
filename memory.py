"""
Memory — 学习记忆层
根据历史交易结果调整策略参数
"""
import json
import threading
try:
    from .db.connection import get_db
    from .config import STRATEGY_CONFIGS
except ImportError:
    from db.connection import get_db
    from config import STRATEGY_CONFIGS


class Memory:
    """
    学习机制：
    - record_outcome(): 每笔平仓后记录
    - get_learned_params(): 读取当前演化后的参数
    - get_strategy_stats(): 各策略胜率统计
    - evolve_params(): 基于结果调整参数
    """

    _evo_lock = threading.Lock()

    @staticmethod
    def record_outcome(trade_id: int, symbol: str, signal_type: str,
                       direction: str, pnl_pct: float, pnl_usd: float,
                       exit_reason: str, holding_hours: float = None):
        """记录交易结果到演化表"""
        conn = get_db()
        c = conn.cursor()
        outcome = "win" if pnl_pct > 0 else "loss"

        # 更新 trades 表的 post_review
        c.execute("UPDATE trades SET post_review=? WHERE id=?", (exit_reason, trade_id))

        # 写入演化记录
        old_cfg = STRATEGY_CONFIGS.get(signal_type, {})
        c.execute("""
            INSERT INTO strategy_evolution
            (signal_type, outcome, pnl_pct, exit_reason, recorded_at)
            VALUES (?, ?, ?, ?, ?)
        """, (
            signal_type,
            outcome,
            round(pnl_pct, 4),
            exit_reason,
            int(__import__("time").time()),
        ))
        conn.commit()

    @staticmethod
    def get_strategy_stats(signal_type: str = None) -> dict:
        """
        获取策略统计数据
        返回 {signal_type: {"total", "wins", "win_rate", "avg_pnl", "avg_holding"}}
        """
        conn = get_db()
        c = conn.cursor()

        if signal_type:
            where = "WHERE signal_type=? AND status='closed'"
            args = (signal_type,)
        else:
            where = "WHERE status='closed'"
            args = ()

        c.execute(f"""
            SELECT
                json_extract(pre_analysis, '$.type') as signal_type,
                COUNT(*) as total,
                SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
                AVG(pnl_pct) as avg_pnl,
                SUM(pnl_usd) as total_pnl,
                SUM(CASE WHEN exit_reason='止损' THEN 1 ELSE 0 END) as sl_count,
                SUM(CASE WHEN exit_reason='止盈' THEN 1 ELSE 0 END) as tp_count
            FROM trades
            {where}
            GROUP BY signal_type
        """, args)

        results = {}
        for row in c.fetchall():
            st = row["signal_type"]
            if not st:
                continue
            total = row["total"] or 0
            wins = row["wins"] or 0
            results[st] = {
                "total": total,
                "wins": wins,
                "losses": total - wins,
                "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
                "avg_pnl": round(row["avg_pnl"] or 0, 2),
                "total_pnl": round(row["total_pnl"] or 0, 4),
                "sl_count": row["sl_count"] or 0,
                "tp_count": row["tp_count"] or 0,
            }
        return results

    @staticmethod
    def get_recent_signals(symbol: str = None, limit: int = 20) -> list:
        """获取最近信号历史"""
        conn = get_db()
        c = conn.cursor()
        if symbol:
            c.execute("""
                SELECT * FROM signal_history
                WHERE symbol=? ORDER BY id DESC LIMIT ?
            """, (symbol, limit))
        else:
            c.execute("""
                SELECT * FROM signal_history
                ORDER BY id DESC LIMIT ?
            """, (limit,))
        return [dict(row) for row in c.fetchall()]

    @staticmethod
    def get_consecutive_losses(symbol: str, lookback: int = 5) -> int:
        """获取最近连续亏损次数"""
        conn = get_db()
        c = conn.cursor()
        c.execute("""
            SELECT pnl_pct FROM trades
            WHERE symbol=? AND status='closed'
            ORDER BY id DESC LIMIT ?
        """, (symbol, lookback))
        rows = c.fetchall()
        losses = 0
        for row in rows:
            if row["pnl_pct"] < 0:
                losses += 1
            else:
                break
        return losses

    @staticmethod
    def evolve_params() -> dict:
        """
        基于历史结果演化策略参数
        调用时机：每10笔平仓后自动触发
        返回演化的参数变化，写入 state.json 并重载配置
        """
        with Memory._evo_lock:
            conn = get_db()
            c = conn.cursor()
            from config import get_strategy_config
            evolved = {}

            for sig_type in STRATEGY_CONFIGS:
                # 获取当前（可能已演化过的）参数
                cfg = get_strategy_config(sig_type).copy()

                # 样本量要求
                c.execute("""
                    SELECT COUNT(*) as n, AVG(pnl_pct) as avg_pnl,
                           SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins
                    FROM trades
                    WHERE json_extract(pre_analysis, '$.type')=?
                    AND status='closed'
                """, (sig_type,))
                row = c.fetchone()
                n = row["n"] or 0
                if n < 5:
                    continue  # 样本不足不演化

                win_rate = (row["wins"] or 0) / n
                avg_pnl = row["avg_pnl"] or 0

                changed = False

                # 规则1：胜率 < 35% → 收紧止损（减少5%）
                if win_rate < 0.35:
                    cfg["sl_pct"] = round(cfg["sl_pct"] * 0.95, 4)
                    changed = True

                # 规则2：胜率 > 65% → 放宽止损（增加3%）
                elif win_rate > 0.65:
                    cfg["sl_pct"] = round(cfg["sl_pct"] * 1.03, 4)
                    changed = True

                # 规则3：止盈被触率 > 60% → 增大止盈（增加10%）
                c.execute("""
                    SELECT COUNT(*) as tp_n FROM trades
                    WHERE json_extract(pre_analysis, '$.type')=?
                    AND exit_reason='止盈' AND status='closed'
                """, (sig_type,))
                tp_n = c.fetchone()["tp_n"] or 0
                if n > 0 and tp_n / n > 0.6:
                    cfg["tp_pct"] = round(cfg["tp_pct"] * 1.10, 4)
                    changed = True

                # 规则4：平均持仓时长 < 1小时 且胜率低 → 减小TP（更快落袋）
                c.execute("""
                    SELECT AVG(
                        (CAST(julianday(exit_time) - julianday(entry_time) AS FLOAT) * 24)
                    ) as avg_hours
                    FROM trades
                    WHERE json_extract(pre_analysis, '$.type')=?
                    AND status='closed' AND exit_time IS NOT NULL
                """, (sig_type,))
                avg_h = c.fetchone()["avg_hours"]
                if avg_h and avg_h < 1.0 and win_rate < 0.45:
                    cfg["tp_pct"] = round(cfg["tp_pct"] * 0.90, 4)
                    changed = True

                if changed:
                    evolved[sig_type] = cfg

            if evolved:
                # 写回 state.json
                from pathlib import Path
                import json as json_lib
                state_path = Path.home() / ".hermes" / "trading_core" / "state.json"
                state_path.parent.mkdir(parents=True, exist_ok=True)
                if state_path.exists():
                    try:
                        with open(state_path) as f:
                            state_data = json_lib.load(f)
                    except Exception:
                        state_data = {}
                else:
                    state_data = {}
                state_data["evolved_params"] = evolved
                state_data["last_evolution"] = int(__import__("time").time())
                with open(state_path, "w") as f:
                    json_lib.dump(state_data, f, indent=2)

                # 重载内存中的全局配置
                from config import reload_strategy_configs
                reload_strategy_configs()

            return evolved
