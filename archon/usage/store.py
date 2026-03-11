"""JSONL persistence helpers for token usage events."""

from __future__ import annotations

import json
import os
from pathlib import Path

from archon.config import STATE_DIR
from archon.usage.models import UsageEvent


def usage_ledger_path(path: Path | None = None) -> Path:
    """Return the token usage ledger path."""
    if path is not None:
        return Path(path)
    return STATE_DIR / "usage" / "ledger.jsonl"


def record_usage_event(event: UsageEvent, path: Path | None = None) -> bool:
    """Append a usage event when both token counts are present."""
    if event.input_tokens is None or event.output_tokens is None:
        return False

    ledger_path = usage_ledger_path(path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(event.to_dict(), ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(ledger_path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)
    return True


def load_usage_events(path: Path | None = None) -> list[UsageEvent]:
    """Load usage events from disk, skipping malformed rows."""
    return list(_iter_usage_events(path))


def summarize_usage_for_session(session_id: str, path: Path | None = None) -> dict:
    """Return usage totals for a session."""
    input_tokens = 0
    output_tokens = 0
    event_count = 0

    for event in _iter_usage_events(path):
        if event.session_id != session_id:
            continue
        if event.input_tokens is None or event.output_tokens is None:
            continue
        input_tokens += event.input_tokens
        output_tokens += event.output_tokens
        event_count += 1

    return {
        "session_id": session_id,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "event_count": event_count,
    }


def summarize_usage_by_source(path: Path | None = None) -> dict[str, dict]:
    """Return usage totals grouped by source."""
    grouped: dict[str, dict[str, int]] = {}

    for event in _iter_usage_events(path):
        if event.input_tokens is None or event.output_tokens is None:
            continue
        bucket = grouped.setdefault(
            event.source,
            {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "event_count": 0,
            },
        )
        bucket["input_tokens"] += event.input_tokens
        bucket["output_tokens"] += event.output_tokens
        bucket["total_tokens"] += event.input_tokens + event.output_tokens
        bucket["event_count"] += 1

    return grouped


def _iter_usage_events(path: Path | None = None):
    """Yield valid usage events from disk, skipping malformed rows."""
    ledger_path = usage_ledger_path(path)
    if not ledger_path.exists():
        return

    with open(ledger_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    continue
                yield UsageEvent.from_dict(payload)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
