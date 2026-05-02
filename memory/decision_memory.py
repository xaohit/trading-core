"""
Decision Memory Loop.

Records structured decision snapshots, reviews them after a horizon, and
archives compact experience cases. This is intentionally rule-based for now;
Hermes/Claude reflection can be layered on top once the memory data is stable.
"""
from __future__ import annotations

import json
import time
from typing import Any

try:
    from .config import (
        DECISION_MEMORY_ENABLED,
        DECISION_REVIEW_HORIZON_HOURS,
        DECISION_JOURNAL_ACTIONS,
    )
    from .db.connection import get_db, init_db
    from .market import Market
except ImportError:
    from config import (
        DECISION_MEMORY_ENABLED,
        DECISION_REVIEW_HORIZON_HOURS,
        DECISION_JOURNAL_ACTIONS,
    )
    from db.connection import get_db, init_db
    from market import Market


def _dumps(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def _loads(value: Any, default):
    if value in (None, ""):
        return default
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


class DecisionMemory:
    @staticmethod
    def record_decision(
        symbol: str,
        action: str,
        signal: dict,
        snapshot: dict | None = None,
        analysis: dict | None = None,
        experiences: list[dict] | None = None,
        result: str | None = None,
        source_trade_id: int | None = None,
        horizon_hours: int | None = None,
        macro_context: dict | None = None,
        market_state: dict | None = None,
        agent_reasoning: str | None = None,
    ) -> int | None:
        """Persist one decision snapshot for later review."""
        if not DECISION_MEMORY_ENABLED or action not in DECISION_JOURNAL_ACTIONS:
            return None

        init_db()
        snapshot = snapshot or {}
        analysis = analysis or {}
        horizon = horizon_hours or DECISION_REVIEW_HORIZON_HOURS
        now = int(time.time())
        due_at = now + int(horizon * 3600)

        direction = signal.get("direction")
        price = _num(signal.get("price") or snapshot.get("price"), 0) or 0
        target_price, invalid_price = DecisionMemory._levels(price, direction, signal)
        tags = DecisionMemory._tags(action, signal, analysis)
        reasoning = DecisionMemory._reasoning(action, signal, analysis, result)
        context = {
            "signal": signal,
            "snapshot": snapshot,
            "analysis": analysis,
            "retrieved_experiences": experiences or [],
            "result": result,
            "market_state": market_state or {},
            "macro_context": macro_context or {},
        }

        conn = get_db()
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO decision_snapshots
            (created_at, due_at, status, symbol, direction, action, signal_type,
             strength, conviction, entry_price, target_price, invalid_price,
             horizon_hours, reasoning, tags, context_json, source_trade_id,
             macro_context, market_state, agent_reasoning)
            VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                due_at,
                symbol,
                direction,
                action,
                signal.get("type"),
                signal.get("strength"),
                _num(analysis.get("score") or signal.get("composite_score"), 0),
                price,
                target_price,
                invalid_price,
                horizon,
                reasoning,
                _dumps(tags),
                _dumps(context),
                source_trade_id,
                _dumps(macro_context),
                _dumps(market_state),
                agent_reasoning,
            ),
        )
        conn.commit()
        return c.lastrowid

    @staticmethod
    def review_due(limit: int = 20) -> list[dict]:
        """Review pending decisions whose horizon has elapsed."""
        init_db()
        now = int(time.time())
        conn = get_db()
        c = conn.cursor()
        c.execute(
            """
            SELECT * FROM decision_snapshots
            WHERE status='pending' AND due_at<=?
            ORDER BY due_at ASC LIMIT ?
            """,
            (now, limit),
        )
        return [DecisionMemory.review_one(dict(row)) for row in c.fetchall()]

    @staticmethod
    def review_one(decision: dict | int) -> dict:
        init_db()
        if isinstance(decision, int):
            row = DecisionMemory.get_decision(decision)
            if not row:
                return {"ok": False, "error": "decision_not_found", "id": decision}
            decision = row

        symbol = decision["symbol"]
        direction = decision.get("direction")
        entry = _num(decision.get("entry_price"), 0) or 0
        if not symbol or not direction or entry <= 0:
            return {"ok": False, "error": "invalid_decision", "id": decision.get("id")}

        review_price = DecisionMemory._current_price(symbol)
        if not review_price:
            return {"ok": False, "error": "missing_price", "id": decision.get("id")}

        mfe, mae = DecisionMemory._excursions(symbol, direction, entry)
        ret = DecisionMemory._return_pct(direction, entry, review_price)
        target_hit = DecisionMemory._target_hit(direction, decision, review_price)
        invalidated = DecisionMemory._invalidated(direction, decision, review_price)
        direction_correct = ret > 0
        label = DecisionMemory._label(direction_correct, target_hit, invalidated)
        tags = _loads(decision.get("tags"), [])
        context = _loads(decision.get("context_json"), {})

        outcome = {
            "decision_id": decision["id"],
            "symbol": symbol,
            "direction": direction,
            "entry_price": entry,
            "review_price": review_price,
            "return_pct": round(ret, 4),
            "max_favorable_pct": mfe,
            "max_adverse_pct": mae,
            "direction_correct": direction_correct,
            "target_hit": target_hit,
            "invalidated": invalidated,
            "outcome_label": label,
        }

        conn = get_db()
        c = conn.cursor()
        c.execute(
            """
            INSERT OR REPLACE INTO decision_outcomes
            (snapshot_id, reviewed_at, review_price, return_pct, max_favorable_pct,
             max_adverse_pct, direction_correct, target_hit, invalidated,
             outcome_label, outcome_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision["id"],
                int(time.time()),
                review_price,
                outcome["return_pct"],
                mfe,
                mae,
                1 if direction_correct else 0,
                1 if target_hit else 0,
                1 if invalidated else 0,
                label,
                _dumps(outcome),
            ),
        )
        c.execute(
            "UPDATE decision_snapshots SET status='reviewed', reviewed_at=? WHERE id=?",
            (int(time.time()), decision["id"]),
        )
        DecisionMemory._archive_experience(c, decision, context, outcome, tags)
        conn.commit()
        return {"ok": True, **outcome}

    @staticmethod
    def get_decision(decision_id: int) -> dict | None:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM decision_snapshots WHERE id=?", (decision_id,))
        row = c.fetchone()
        return dict(row) if row else None

    @staticmethod
    def recent_decisions(limit: int = 20) -> list[dict]:
        init_db()
        conn = get_db()
        c = conn.cursor()
        c.execute(
            """
            SELECT d.*, o.outcome_label, o.return_pct, o.max_favorable_pct,
                   o.max_adverse_pct, o.direction_correct, o.target_hit, o.invalidated
            FROM decision_snapshots d
            LEFT JOIN decision_outcomes o ON o.snapshot_id=d.id
            ORDER BY d.id DESC LIMIT ?
            """,
            (limit,),
        )
        return [DecisionMemory._format_decision(dict(row)) for row in c.fetchall()]

    @staticmethod
    def reviewed_decisions(limit: int = 200) -> list[dict]:
        """Return historical reviewed decisions with joined outcome fields."""
        init_db()
        conn = get_db()
        c = conn.cursor()
        c.execute(
            """
            SELECT d.*, o.review_price, o.return_pct, o.max_favorable_pct,
                   o.max_adverse_pct, o.direction_correct, o.target_hit,
                   o.invalidated, o.outcome_label, o.outcome_json,
                   o.reviewed_at AS outcome_reviewed_at
            FROM decision_snapshots d
            JOIN decision_outcomes o ON o.snapshot_id=d.id
            WHERE d.status='reviewed'
            ORDER BY COALESCE(o.reviewed_at, d.reviewed_at, d.id) DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in c.fetchall()]

    @staticmethod
    def recent_experiences(limit: int = 20) -> list[dict]:
        init_db()
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM experience_cases ORDER BY id DESC LIMIT ?", (limit,))
        rows = []
        for row in c.fetchall():
            item = dict(row)
            item["tags"] = _loads(item.get("tags"), [])
            item["adjustment"] = _loads(item.get("adjustment_json"), {})
            rows.append(item)
        return rows

    @staticmethod
    def retrieve_similar(
        symbol: str | None = None,
        signal_type: str | None = None,
        tags: list[str] | None = None,
        limit: int = 5,
    ) -> list[dict]:
        """Return similar experience cases using transparent weighted matching."""
        init_db()
        query_tags = set(tags or [])
        conn = get_db()
        c = conn.cursor()
        c.execute(
            """
            SELECT * FROM experience_cases
            ORDER BY id DESC LIMIT 300
            """
        )
        scored = []
        for row in c.fetchall():
            item = dict(row)
            item_tags = set(_loads(item.get("tags"), []))
            score = 0.0
            if symbol and item.get("symbol") == symbol:
                score += 5
            if signal_type and item.get("signal_type") == signal_type:
                score += 4
            overlap = sorted(query_tags & item_tags)
            score += min(len(overlap), 8) * 1.5
            if item.get("outcome_label") in {"direction_wrong", "invalidated"}:
                score += 1.0
            # Small recency nudge; ids are monotonic and good enough for v1.
            score += min(float(item.get("id") or 0) / 10000.0, 0.5)
            if score <= 0:
                continue
            item["tags"] = sorted(item_tags)
            item["adjustment"] = _loads(item.get("adjustment_json"), {})
            item["similarity_score"] = round(score, 3)
            item["matched_tags"] = overlap
            scored.append(item)
        scored.sort(key=lambda x: x["similarity_score"], reverse=True)
        return scored[:limit]

    @staticmethod
    def retrieve_for_signal(symbol: str, signal: dict, analysis: dict, limit: int = 5) -> list[dict]:
        tags = DecisionMemory._tags("lookup", signal, analysis)
        return DecisionMemory.retrieve_similar(
            symbol=symbol,
            signal_type=signal.get("type"),
            tags=tags,
            limit=limit,
        )

    @staticmethod
    def reflection_prompt(decision_id: int) -> str | None:
        decision = DecisionMemory.get_decision(decision_id)
        if not decision:
            return None
        context = _loads(decision.get("context_json"), {})
        return (
            f"你在 {decision.get('symbol')} 上做了一个 {decision.get('direction')} 判断，"
            f"动作={decision.get('action')}，conviction={decision.get('conviction')}。\n"
            f"当时理由：{decision.get('reasoning')}\n"
            f"当时上下文 JSON：{json.dumps(context, ensure_ascii=False)}\n\n"
            "请复盘：1. 哪个信号判断失误或有效？2. 忽略了什么？"
            "3. 下次类似场景如何调整？请输出结构化经验。"
        )

    @staticmethod
    def _levels(price: float, direction: str | None, signal: dict) -> tuple[float | None, float | None]:
        if price <= 0 or direction not in {"long", "short"}:
            return None, None
        sl_pct = _num(signal.get("sl_pct"), None)
        tp_pct = _num(signal.get("tp_pct"), None)
        if direction == "long":
            target = price * (1 + tp_pct) if tp_pct is not None else None
            invalid = price * (1 - sl_pct) if sl_pct is not None else None
        else:
            target = price * (1 - tp_pct) if tp_pct is not None else None
            invalid = price * (1 + sl_pct) if sl_pct is not None else None
        return (
            round(target, 8) if target else None,
            round(invalid, 8) if invalid else None,
        )

    @staticmethod
    def _tags(action: str, signal: dict, analysis: dict) -> list[str]:
        tags = set(analysis.get("tags") or [])
        for key in ("type", "strength", "direction"):
            if signal.get(key):
                tags.add(f"{key}:{signal[key]}")
        tags.add(f"action:{action}")
        verdict = analysis.get("verdict")
        if verdict:
            tags.add(f"verdict:{verdict}")
        return sorted(tags)

    @staticmethod
    def _reasoning(action: str, signal: dict, analysis: dict, result: str | None) -> str:
        parts = [
            f"action={action}",
            f"signal={signal.get('type')}/{signal.get('strength')}",
            f"direction={signal.get('direction')}",
            f"score={analysis.get('score')}",
            f"verdict={analysis.get('verdict')}",
        ]
        if signal.get("reason"):
            parts.append(f"reason={signal.get('reason')}")
        if result:
            parts.append(f"result={result}")
        return " | ".join(str(p) for p in parts if p)

    @staticmethod
    def _current_price(symbol: str) -> float | None:
        ticker = Market.ticker(symbol)
        return _num((ticker or {}).get("lastPrice"), None)

    @staticmethod
    def _excursions(symbol: str, direction: str, entry: float) -> tuple[float | None, float | None]:
        klines = Market.klines(symbol, "5m", 288)
        highs = []
        lows = []
        for row in klines:
            if isinstance(row, list) and len(row) >= 5:
                high = _num(row[2], None)
                low = _num(row[3], None)
                if high is not None:
                    highs.append(high)
                if low is not None:
                    lows.append(low)
        if not highs or not lows:
            return None, None
        if direction == "long":
            mfe = (max(highs) - entry) / entry * 100
            mae = (min(lows) - entry) / entry * 100
        else:
            mfe = (entry - min(lows)) / entry * 100
            mae = (entry - max(highs)) / entry * 100
        return round(mfe, 4), round(mae, 4)

    @staticmethod
    def _return_pct(direction: str, entry: float, review_price: float) -> float:
        if direction == "long":
            return (review_price - entry) / entry * 100
        return (entry - review_price) / entry * 100

    @staticmethod
    def _target_hit(direction: str, decision: dict, review_price: float) -> bool:
        target = _num(decision.get("target_price"), None)
        if target is None:
            return False
        return review_price >= target if direction == "long" else review_price <= target

    @staticmethod
    def _invalidated(direction: str, decision: dict, review_price: float) -> bool:
        invalid = _num(decision.get("invalid_price"), None)
        if invalid is None:
            return False
        return review_price <= invalid if direction == "long" else review_price >= invalid

    @staticmethod
    def _label(direction_correct: bool, target_hit: bool, invalidated: bool) -> str:
        if target_hit:
            return "target_hit"
        if invalidated:
            return "invalidated"
        if direction_correct:
            return "direction_correct"
        return "direction_wrong"

    @staticmethod
    def _archive_experience(cursor, decision: dict, context: dict, outcome: dict, tags: list[str]):
        signal = context.get("signal", {})
        analysis = context.get("analysis", {})
        snapshot = context.get("snapshot", {})
        lesson, adjustment = DecisionMemory._lesson(outcome, analysis, snapshot)
        searchable = " ".join([
            decision.get("symbol") or "",
            signal.get("type") or "",
            outcome.get("outcome_label") or "",
            lesson,
            " ".join(tags),
        ])
        cursor.execute(
            """
            INSERT INTO experience_cases
            (source_snapshot_id, symbol, signal_type, outcome_label, tags,
             lesson, adjustment_json, searchable_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision["id"],
                decision.get("symbol"),
                signal.get("type") or decision.get("signal_type"),
                outcome.get("outcome_label"),
                _dumps(tags),
                lesson,
                _dumps(adjustment),
                searchable,
            ),
        )

    @staticmethod
    def _lesson(outcome: dict, analysis: dict, snapshot: dict) -> tuple[str, dict]:
        tags = set(analysis.get("tags") or [])
        if outcome["outcome_label"] in {"target_hit", "direction_correct"}:
            return (
                "This setup worked; keep the same signal family but continue requiring liquidity and score confirmation.",
                {"conviction_delta": 2, "requires_extra_confirmation": False},
            )
        if "price_overheated" in tags or "funding_hot" in tags or "long_crowded" in tags:
            return (
                "Crowded or overheated conditions weakened the signal; discount conviction next time.",
                {"conviction_delta": -12, "requires_extra_confirmation": True},
            )
        if "buy_pressure_falling" in tags or "taker_weak" in tags:
            return (
                "Taker pressure faded; require fresh buy/sell pressure confirmation before acting.",
                {"conviction_delta": -8, "requires_extra_confirmation": True},
            )
        if outcome["invalidated"]:
            return (
                "The invalidation level was reached; similar setups need tighter entry timing or smaller size.",
                {"conviction_delta": -10, "requires_extra_confirmation": True},
            )
        return (
            "Direction was wrong after review horizon; archive as caution until similar samples accumulate.",
            {"conviction_delta": -6, "requires_extra_confirmation": True},
        )

    @staticmethod
    def _format_decision(row: dict) -> dict:
        row["tags"] = _loads(row.get("tags"), [])
        row["context"] = _loads(row.get("context_json"), {})
        row.pop("context_json", None)
        return row
