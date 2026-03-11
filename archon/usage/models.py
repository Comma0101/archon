"""Models for persistent token accounting."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
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
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "UsageEvent":
        return cls(
            event_id=_require_nonempty_str(data.get("event_id"), "event_id"),
            session_id=_require_nonempty_str(data.get("session_id"), "session_id"),
            turn_id=_require_nonempty_str(data.get("turn_id"), "turn_id"),
            source=_require_nonempty_str(data.get("source"), "source"),
            provider=_require_nonempty_str(data.get("provider"), "provider"),
            model=_require_nonempty_str(data.get("model"), "model"),
            input_tokens=_coerce_optional_int(data.get("input_tokens")),
            output_tokens=_coerce_optional_int(data.get("output_tokens")),
            recorded_at=float(data.get("recorded_at", 0.0) or 0.0),
        )


def _coerce_optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def _require_nonempty_str(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"Missing {field_name}")
    return text
