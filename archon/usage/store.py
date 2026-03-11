"""Append-only JSONL persistence for usage events."""

from __future__ import annotations

import json
from pathlib import Path

from archon.config import STATE_DIR
from archon.usage.models import UsageEvent


USAGE_STATE_DIR = STATE_DIR / "usage"


def usage_ledger_path(path: Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    return USAGE_STATE_DIR / "usage.jsonl"


def _summary_template() -> dict[str, int]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "event_count": 0,
    }


def _include_event(summary: dict[str, int], event: UsageEvent) -> None:
    if event.input_tokens is None or event.output_tokens is None:
        return
    summary["input_tokens"] += event.input_tokens
    summary["output_tokens"] += event.output_tokens
    summary["total_tokens"] += event.input_tokens + event.output_tokens
    summary["event_count"] += 1


def record_usage_event(event: UsageEvent, path: Path | None = None) -> bool:
    if event.input_tokens is None or event.output_tokens is None:
        return False

    ledger_path = usage_ledger_path(path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with open(ledger_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
    return True


def load_usage_events(path: Path | None = None) -> list[UsageEvent]:
    ledger_path = usage_ledger_path(path)
    if not ledger_path.exists():
        return []

    events: list[UsageEvent] = []
    with open(ledger_path, "r", encoding="utf-8") as f:
        for line in f:
            row = line.strip()
            if not row:
                continue
            try:
                data = json.loads(row)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            events.append(UsageEvent.from_dict(data))
    return events


def summarize_usage_for_session(
    session_id: str,
    path: Path | None = None,
) -> dict[str, str | int]:
    summary: dict[str, str | int] = {
        "session_id": session_id,
        **_summary_template(),
    }
    for event in load_usage_events(path=path):
        if event.session_id != session_id:
            continue
        _include_event(summary, event)
    return summary


def summarize_usage_by_source(path: Path | None = None) -> dict[str, dict[str, int]]:
    summaries: dict[str, dict[str, int]] = {}
    for event in load_usage_events(path=path):
        summary = summaries.setdefault(event.source, _summary_template())
        _include_event(summary, event)
    return summaries
