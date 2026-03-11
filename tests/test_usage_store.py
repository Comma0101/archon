"""Tests for persistent usage ledger storage."""

import json


def _usage_event(**overrides):
    from archon.usage.models import UsageEvent

    payload = {
        "event_id": "evt-1",
        "session_id": "session-1",
        "turn_id": "turn-1",
        "source": "chat",
        "provider": "openai",
        "model": "gpt-4o-mini",
        "input_tokens": 11,
        "output_tokens": 7,
        "recorded_at": 1_700_000_000.0,
    }
    payload.update(overrides)
    return UsageEvent(**payload)


def _read_jsonl(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class TestUsageStore:
    def test_record_usage_event_appends_jsonl_entry(self, monkeypatch, tmp_path):
        from archon.usage.store import record_usage_event

        ledger_path = tmp_path / "usage" / "ledger.jsonl"
        monkeypatch.setattr("archon.usage.store.USAGE_LEDGER_PATH", ledger_path)

        record_usage_event(_usage_event())
        record_usage_event(
            _usage_event(
                event_id="evt-2",
                turn_id="turn-2",
                input_tokens=3,
                output_tokens=2,
                recorded_at=1_700_000_001.0,
            )
        )

        rows = _read_jsonl(ledger_path)
        assert len(rows) == 2
        assert rows[0]["event_id"] == "evt-1"
        assert rows[0]["session_id"] == "session-1"
        assert rows[0]["source"] == "chat"
        assert rows[0]["input_tokens"] == 11
        assert rows[0]["output_tokens"] == 7
        assert rows[1]["event_id"] == "evt-2"
        assert rows[1]["turn_id"] == "turn-2"

    def test_summarize_usage_for_session_totals_input_and_output_tokens(
        self, monkeypatch, tmp_path
    ):
        from archon.usage.store import record_usage_event, summarize_usage_for_session

        ledger_path = tmp_path / "usage" / "ledger.jsonl"
        monkeypatch.setattr("archon.usage.store.USAGE_LEDGER_PATH", ledger_path)

        record_usage_event(_usage_event(input_tokens=11, output_tokens=7))
        record_usage_event(
            _usage_event(
                event_id="evt-2",
                turn_id="turn-2",
                source="news",
                input_tokens=5,
                output_tokens=3,
                recorded_at=1_700_000_001.0,
            )
        )
        record_usage_event(
            _usage_event(
                event_id="evt-3",
                session_id="session-2",
                turn_id="turn-1",
                input_tokens=99,
                output_tokens=88,
                recorded_at=1_700_000_002.0,
            )
        )

        summary = summarize_usage_for_session("session-1")

        assert summary["session_id"] == "session-1"
        assert summary["event_count"] == 2
        assert summary["input_tokens"] == 16
        assert summary["output_tokens"] == 10

    def test_summarize_usage_by_source_keeps_chat_and_news_distinct(
        self, monkeypatch, tmp_path
    ):
        from archon.usage.store import record_usage_event, summarize_usage_by_source

        ledger_path = tmp_path / "usage" / "ledger.jsonl"
        monkeypatch.setattr("archon.usage.store.USAGE_LEDGER_PATH", ledger_path)

        record_usage_event(_usage_event(input_tokens=11, output_tokens=7))
        record_usage_event(
            _usage_event(
                event_id="evt-2",
                turn_id="turn-2",
                source="chat",
                input_tokens=13,
                output_tokens=5,
                recorded_at=1_700_000_001.0,
            )
        )
        record_usage_event(
            _usage_event(
                event_id="evt-3",
                turn_id="turn-3",
                source="news",
                input_tokens=17,
                output_tokens=4,
                recorded_at=1_700_000_002.0,
            )
        )

        grouped = summarize_usage_by_source(session_id="session-1")

        assert set(grouped) == {"chat", "news"}
        assert grouped["chat"]["event_count"] == 2
        assert grouped["chat"]["input_tokens"] == 24
        assert grouped["chat"]["output_tokens"] == 12
        assert grouped["news"]["event_count"] == 1
        assert grouped["news"]["input_tokens"] == 17
        assert grouped["news"]["output_tokens"] == 4

    def test_record_usage_event_ignores_missing_usage_data(
        self, monkeypatch, tmp_path
    ):
        from archon.usage.store import (
            record_usage_event,
            summarize_usage_by_source,
            summarize_usage_for_session,
        )

        ledger_path = tmp_path / "usage" / "ledger.jsonl"
        monkeypatch.setattr("archon.usage.store.USAGE_LEDGER_PATH", ledger_path)

        record_usage_event(_usage_event(input_tokens=11, output_tokens=7))
        record_usage_event(
            _usage_event(
                event_id="evt-2",
                turn_id="turn-2",
                source="news",
                input_tokens=None,
                output_tokens=None,
                recorded_at=1_700_000_001.0,
            )
        )

        summary = summarize_usage_for_session("session-1")
        grouped = summarize_usage_by_source(session_id="session-1")
        rows = _read_jsonl(ledger_path)

        assert summary["event_count"] == 1
        assert summary["input_tokens"] == 11
        assert summary["output_tokens"] == 7
        assert set(grouped) == {"chat"}
        assert len(rows) == 1
        assert rows[0]["event_id"] == "evt-1"
