import time
import json

from db.connection import get_db
from decision_memory import DecisionMemory


def main():
    symbol = "TESTUSDT"
    decision_id = DecisionMemory.record_decision(
        symbol=symbol,
        action="score_reject",
        signal={
            "type": "smoke_signal",
            "strength": "A",
            "direction": "long",
            "price": 10.0,
            "sl_pct": 0.03,
            "tp_pct": 0.08,
            "reason": "smoke",
        },
        snapshot={"symbol": symbol, "price": 10.0, "funding_rate": 0.01},
        analysis={
            "score": 55,
            "verdict": "smoke",
            "tags": ["smoke_tag"],
            "notes": ["smoke note"],
        },
        result="smoke",
        horizon_hours=24,
    )
    assert decision_id, "decision was not recorded"
    recent = DecisionMemory.recent_decisions(5)
    assert any(d["id"] == decision_id for d in recent), "decision not found"

    conn = get_db()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO experience_cases
        (source_snapshot_id, symbol, signal_type, outcome_label, tags,
         lesson, adjustment_json, searchable_text)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            decision_id,
            symbol,
            "smoke_signal",
            "direction_wrong",
            json.dumps(["smoke_tag", "type:smoke_signal"], ensure_ascii=False),
            "smoke lesson",
            json.dumps({"conviction_delta": -1}, ensure_ascii=False),
            "smoke searchable text",
        ),
    )
    exp_id = c.lastrowid
    conn.commit()

    matches = DecisionMemory.retrieve_similar(
        symbol=symbol,
        signal_type="smoke_signal",
        tags=["smoke_tag"],
        limit=3,
    )
    assert matches and matches[0]["id"] == exp_id, "similar experience not retrieved"

    c.execute("DELETE FROM decision_snapshots WHERE id=?", (decision_id,))
    c.execute("DELETE FROM experience_cases WHERE id=?", (exp_id,))
    conn.commit()
    print("DECISION_MEMORY_SMOKE_OK", decision_id, int(time.time()))


if __name__ == "__main__":
    main()
