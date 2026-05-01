"""
Lightweight semantic radar.

This module is intentionally conservative. It does not trade and does not call
LLMs. It only provides a structured place for news/macro/KOL/Polymarket style
events to be attached to an Agent decision.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass


@dataclass
class SemanticEvent:
    symbol: str
    event_type: str
    severity: int
    direction_hint: str
    summary: str
    source: str = "manual"
    created_at: int = 0

    def to_dict(self) -> dict:
        data = asdict(self)
        if not data["created_at"]:
            data["created_at"] = int(time.time())
        return data


class SemanticRadar:
    """In-memory event radar placeholder."""

    _events: list[dict] = []

    @classmethod
    def add_event(
        cls,
        symbol: str,
        event_type: str,
        severity: int,
        direction_hint: str,
        summary: str,
        source: str = "manual",
    ) -> dict:
        event = SemanticEvent(
            symbol=symbol,
            event_type=event_type,
            severity=max(0, min(100, int(severity))),
            direction_hint=direction_hint,
            summary=summary,
            source=source,
        ).to_dict()
        cls._events.append(event)
        cls._events = cls._events[-200:]
        return event

    @classmethod
    def events_for(cls, symbol: str, min_severity: int = 50, limit: int = 5) -> list[dict]:
        events = [
            event
            for event in cls._events
            if event.get("symbol") in {symbol, "GLOBAL"}
            and (event.get("severity") or 0) >= min_severity
        ]
        events.sort(key=lambda item: (item.get("severity") or 0, item.get("created_at") or 0), reverse=True)
        return events[:limit]

    @classmethod
    def recent(cls, limit: int = 20) -> list[dict]:
        return list(reversed(cls._events[-limit:]))
