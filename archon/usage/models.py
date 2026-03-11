"""Data models for usage ledger events."""

from __future__ import annotations

from dataclasses import dataclass


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass
class UsageEvent:
    event_id: str
    session_id: str
    turn_id: str
    source: str
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    recorded_at: float

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "source": self.source,
            "provider": self.provider,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "recorded_at": self.recorded_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "UsageEvent":
        return cls(
            event_id=str(data.get("event_id", "")),
            session_id=str(data.get("session_id", "")),
            turn_id=str(data.get("turn_id", "")),
            source=str(data.get("source", "")),
            provider=str(data.get("provider", "")),
            model=str(data.get("model", "")),
            input_tokens=_optional_int(data.get("input_tokens")),
            output_tokens=_optional_int(data.get("output_tokens")),
            recorded_at=float(data.get("recorded_at", 0) or 0),
        )
