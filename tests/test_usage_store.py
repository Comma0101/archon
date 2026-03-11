from __future__ import annotations

import json

import pytest

REQUIRED_ROW_KEYS = {
    "event_id",
    "session_id",
    "turn_id",
    "source",
    "provider",
    "model",
    "input_tokens",
    "output_tokens",
    "recorded_at",
}


def _usage_api():
    from archon.usage.models import UsageEvent
    from archon.usage.store import (
        load_usage_events,
        record_usage_event,
        summarize_usage_by_source,
        summarize_usage_for_session,
    )

    return (
        UsageEvent,
        load_usage_events,
        record_usage_event,
        summarize_usage_by_source,
        summarize_usage_for_session,
    )


def _event(
    *,
    event_id: str = "evt-1",
    session_id: str = "sess-1",
    turn_id: str = "t001",
    source: str = "chat",
    provider: str = "google",
    model: str = "gemini-3.1-pro-preview",
    input_tokens: int | None = 10,
    output_tokens: int | None = 5,
    recorded_at: float = 1_700_000_000.0,
) -> object:
    UsageEvent, *_rest = _usage_api()
    return UsageEvent(
        event_id=event_id,
        session_id=session_id,
        turn_id=turn_id,
        source=source,
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        recorded_at=recorded_at,
    )


def _read_jsonl(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class TestUsageStore:
    def test_record_usage_event_appends_jsonl_and_loads_it_back(self, tmp_path):
        _, load_usage_events, record_usage_event, _, _ = _usage_api()
        ledger_path = tmp_path / "usage.jsonl"

        recorded = record_usage_event(_event(), path=ledger_path)
        recorded_second = record_usage_event(
            _event(
                event_id="evt-2",
                turn_id="t002",
                input_tokens=7,
                output_tokens=2,
                recorded_at=1_700_000_001.0,
            ),
            path=ledger_path,
        )

        assert recorded is True
        assert recorded_second is True
        assert ledger_path.exists()
        rows = _read_jsonl(ledger_path)
        assert len(rows) == 2
        assert rows[0]["event_id"] == "evt-1"
        assert rows[1]["event_id"] == "evt-2"
        assert REQUIRED_ROW_KEYS <= set(rows[0])
        assert rows[0]["input_tokens"] == 10
        assert rows[0]["output_tokens"] == 5
        assert isinstance(rows[0]["recorded_at"], float)
        events = load_usage_events(path=ledger_path)
        assert len(events) == 2
        assert events[0].event_id == "evt-1"
        assert events[0].session_id == "sess-1"
        assert events[0].input_tokens == 10
        assert events[0].output_tokens == 5
        assert events[1].event_id == "evt-2"

    def test_summarize_usage_for_session_totals_input_and_output_tokens(self, tmp_path):
        _, _, record_usage_event, _, summarize_usage_for_session = _usage_api()
        ledger_path = tmp_path / "usage.jsonl"
        record_usage_event(
            _event(
                event_id="evt-1",
                session_id="sess-1",
                input_tokens=10,
                output_tokens=5,
            ),
            path=ledger_path,
        )
        record_usage_event(
            _event(
                event_id="evt-2",
                session_id="sess-1",
                turn_id="t002",
                input_tokens=7,
                output_tokens=2,
                recorded_at=1_700_000_001.0,
            ),
            path=ledger_path,
        )
        record_usage_event(
            _event(
                event_id="evt-3",
                session_id="sess-2",
                turn_id="t001",
                input_tokens=99,
                output_tokens=1,
                recorded_at=1_700_000_002.0,
            ),
            path=ledger_path,
        )

        summary = summarize_usage_for_session("sess-1", path=ledger_path)

        assert summary["session_id"] == "sess-1"
        assert summary["input_tokens"] == 17
        assert summary["output_tokens"] == 7
        assert summary["total_tokens"] == 24
        assert summary["event_count"] == 2

    def test_summarize_usage_by_source_keeps_chat_and_news_distinct(self, tmp_path):
        _, _, record_usage_event, summarize_usage_by_source, _ = _usage_api()
        ledger_path = tmp_path / "usage.jsonl"
        record_usage_event(
            _event(
                event_id="evt-1",
                source="chat",
                input_tokens=10,
                output_tokens=5,
            ),
            path=ledger_path,
        )
        record_usage_event(
            _event(
                event_id="evt-2",
                source="news",
                turn_id="news-1",
                input_tokens=4,
                output_tokens=1,
                recorded_at=1_700_000_001.0,
            ),
            path=ledger_path,
        )
        record_usage_event(
            _event(
                event_id="evt-3",
                source="chat",
                turn_id="t002",
                input_tokens=7,
                output_tokens=2,
                recorded_at=1_700_000_002.0,
            ),
            path=ledger_path,
        )

        summary = summarize_usage_by_source(path=ledger_path)

        assert summary["chat"]["input_tokens"] == 17
        assert summary["chat"]["output_tokens"] == 7
        assert summary["chat"]["total_tokens"] == 24
        assert summary["chat"]["event_count"] == 2
        assert summary["news"]["input_tokens"] == 4
        assert summary["news"]["output_tokens"] == 1
        assert summary["news"]["total_tokens"] == 5
        assert summary["news"]["event_count"] == 1

    @pytest.mark.parametrize(
        ("input_tokens", "output_tokens"),
        [
            (None, None),
            (None, 5),
            (5, None),
        ],
    )
    def test_record_usage_event_ignores_missing_usage_data(
        self,
        tmp_path,
        input_tokens,
        output_tokens,
    ):
        _, load_usage_events, record_usage_event, _, _ = _usage_api()
        ledger_path = tmp_path / "usage.jsonl"

        recorded = record_usage_event(
            _event(
                event_id="evt-empty",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            ),
            path=ledger_path,
        )

        assert recorded is False
        assert ledger_path.exists() is False
        assert load_usage_events(path=ledger_path) == []
