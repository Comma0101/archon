"""Tests for research job storage, polling, and stream handshake."""

import json
import time
from pathlib import Path

from archon.config import Config
from archon.research.models import ResearchJobRecord
from archon.research.store import (
    consume_research_stream,
    start_research_stream_job,
    ensure_research_recovery_started,
    save_research_job,
    cancel_research_job,
    load_research_job,
    poll_research_job,
)


def test_cancel_research_job(tmp_path, monkeypatch):
    monkeypatch.setattr("archon.research.store.RESEARCH_JOBS_DIR", tmp_path)

    record = ResearchJobRecord(
        interaction_id="test-123",
        status="in_progress",
        prompt="test query",
        agent="test-agent",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        summary="Running",
        output_text="",
        error="",
    )
    save_research_job(record)

    result = cancel_research_job("test-123", reason="User requested cancellation")
    assert result is not None
    assert result.status == "cancelled"
    assert "User requested" in result.error

    reloaded = load_research_job("test-123")
    assert reloaded.status == "cancelled"


def test_cancel_nonexistent_job(tmp_path, monkeypatch):
    monkeypatch.setattr("archon.research.store.RESEARCH_JOBS_DIR", tmp_path)
    result = cancel_research_job("nonexistent")
    assert result is None


def test_cancelled_research_job_is_not_overwritten_by_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr("archon.research.store.RESEARCH_JOBS_DIR", tmp_path)

    record = ResearchJobRecord(
        interaction_id="test-123",
        status="in_progress",
        prompt="test query",
        agent="test-agent",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        summary="Running",
        output_text="",
        error="",
    )
    save_research_job(record)
    cancel_research_job("test-123", reason="User requested cancellation")

    class _RefreshClient:
        def get_research(self, interaction_id: str):
            assert interaction_id == "test-123"
            return type(
                "_Interaction",
                (),
                {"status": "in_progress", "output_text": "", "interaction_id": interaction_id},
            )()

    refreshed = load_research_job("test-123", refresh_client=_RefreshClient())

    assert refreshed is not None
    assert refreshed.status == "cancelled"
    assert refreshed.summary == "Research job cancelled"


def test_poll_research_job_uses_config_backed_google_client(tmp_path, monkeypatch):
    monkeypatch.setattr("archon.research.store.RESEARCH_JOBS_DIR", tmp_path)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    cfg = Config()
    cfg.llm.provider = "google"
    cfg.llm.api_key = "cfg-google-key"
    cfg.research.google_deep_research.enabled = True
    monkeypatch.setattr("archon.research.store.load_config", lambda: cfg)

    save_research_job(
        ResearchJobRecord(
            interaction_id="test-123",
            status="in_progress",
            prompt="test query",
            agent="deep-research-pro-preview-12-2025",
            created_at="2099-01-01T00:00:00Z",
            updated_at="2099-01-01T00:00:00Z",
            summary="Running",
            output_text="",
            error="",
        )
    )

    captured = {}

    class _RefreshClient:
        def get_research(self, interaction_id: str):
            assert interaction_id == "test-123"
            return type(
                "_Interaction",
                (),
                {"status": "completed", "output_text": "Final report", "interaction_id": interaction_id},
            )()

    def _fake_from_api_key(api_key: str, *, agent: str = "", thinking_summaries: str = "auto"):
        captured["api_key"] = api_key
        captured["agent"] = agent
        captured["thinking_summaries"] = thinking_summaries
        return _RefreshClient()

    monkeypatch.setattr(
        "archon.research.store.GoogleDeepResearchClient.from_api_key",
        _fake_from_api_key,
    )

    refreshed = poll_research_job("test-123")

    assert refreshed is not None
    assert refreshed.status == "completed"
    assert refreshed.summary == "Final report"
    assert captured == {
        "api_key": "cfg-google-key",
        "agent": "deep-research-pro-preview-12-2025",
        "thinking_summaries": "auto",
    }


def test_consume_research_stream_persists_progress_and_completion(tmp_path, monkeypatch):
    monkeypatch.setattr("archon.research.store.RESEARCH_JOBS_DIR", tmp_path)

    save_research_job(
        ResearchJobRecord(
            interaction_id="stream-123",
            status="in_progress",
            prompt="test query",
            agent="deep-research-pro-preview-12-2025",
            created_at="2099-01-01T00:00:00Z",
            updated_at="2099-01-01T00:00:00Z",
            summary="Research job started",
            output_text="",
            error="",
        )
    )

    class _Event:
        def __init__(self, event_type: str, **fields):
            self.event_type = event_type
            for key, value in fields.items():
                setattr(self, key, value)

    events = iter(
        [
            _Event(
                "content.delta",
                delta={"type": "thought_summary", "text": "Checking sources"},
            ),
            _Event(
                "interaction.complete",
                interaction={"id": "stream-123", "status": "completed"},
                text="Final answer",
            ),
        ]
    )

    consume_research_stream("stream-123", events)

    record = load_research_job("stream-123")
    assert record is not None
    assert record.status == "completed"
    assert record.summary == "Final answer"
    assert record.output_text == "Final answer"
    assert record.event_count == 2
    assert record.poll_count == 0


def test_consume_research_stream_marks_unfinished_stream_as_error(tmp_path, monkeypatch):
    monkeypatch.setattr("archon.research.store.RESEARCH_JOBS_DIR", tmp_path)

    save_research_job(
        ResearchJobRecord(
            interaction_id="stream-123",
            status="in_progress",
            prompt="test query",
            agent="deep-research-pro-preview-12-2025",
            created_at="2099-01-01T00:00:00Z",
            updated_at="2099-01-01T00:00:00Z",
            summary="Research job started",
            output_text="",
            error="",
            stream_status="interaction.status_update",
        )
    )

    class _Event:
        def __init__(self, event_type: str, **fields):
            self.event_type = event_type
            for key, value in fields.items():
                setattr(self, key, value)

    events = iter(
        [
            _Event("interaction.status_update", status="in_progress"),
        ]
    )

    consume_research_stream("stream-123", events)

    record = load_research_job("stream-123")
    assert record is not None
    assert record.status == "error"
    assert record.summary == "Research stream ended before completion"
    assert "ended before completion" in record.error


def test_start_research_stream_job_saves_record_and_starts_monitor(tmp_path, monkeypatch):
    """Worker saves initial record and starts poll monitor, then exits."""
    monkeypatch.setattr("archon.research.store.RESEARCH_JOBS_DIR", tmp_path)

    class _Stream:
        interaction_id = "stream-job-123"
        status = "in_progress"

        def __init__(self):
            self.events = iter(())

    class _Client:
        def __init__(self):
            self.prompts = []

        def start_research_stream(self, prompt: str):
            self.prompts.append(prompt)
            return _Stream()

    monitor_calls = []

    def _fake_start_monitor(interaction_id: str, *, refresh_client, poll_interval_sec=10, hook_bus=None):
        monitor_calls.append(interaction_id)
        return True

    monkeypatch.setattr("archon.research.store.start_research_job_monitor", _fake_start_monitor)

    client = _Client()
    record = start_research_stream_job(
        "test query",
        client=client,
        agent_name="deep-research-pro-preview-12-2025",
        timeout_minutes=20,
    )

    assert record is not None
    assert record.interaction_id == "stream-job-123"
    assert client.prompts == ["test query"]
    assert monitor_calls == ["stream-job-123"]

    reloaded = load_research_job("stream-job-123")
    assert reloaded is not None
    assert reloaded.status == "in_progress"
    assert reloaded.stream_status == "started"


def test_start_research_stream_job_starts_poll_monitor(tmp_path, monkeypatch):
    monkeypatch.setattr("archon.research.store.RESEARCH_JOBS_DIR", tmp_path)

    class _Stream:
        interaction_id = "stream-job-monitor-123"
        status = "in_progress"

        def __init__(self):
            self.events = iter(())

    class _Client:
        def start_research_stream(self, prompt: str):
            return _Stream()

    monitor_calls = []

    def _fake_start_monitor(interaction_id: str, *, refresh_client, poll_interval_sec=10, hook_bus=None):
        from archon.research import store as research_store

        monitor_calls.append(
            {
                "interaction_id": interaction_id,
                "refresh_client": refresh_client,
                "poll_interval_sec": poll_interval_sec,
                "hook_bus": hook_bus,
                "monitor_slot_preexisting": interaction_id in research_store._RESEARCH_MONITORS,
            }
        )
        return True

    monkeypatch.setattr("archon.research.store.start_research_job_monitor", _fake_start_monitor)

    record = start_research_stream_job(
        "test query",
        client=_Client(),
        agent_name="deep-research-pro-preview-12-2025",
        timeout_minutes=20,
    )

    assert record is not None
    assert monitor_calls
    assert monitor_calls[0]["interaction_id"] == "stream-job-monitor-123"
    assert monitor_calls[0]["poll_interval_sec"] == 10
    assert monitor_calls[0]["monitor_slot_preexisting"] is False


def test_ensure_research_recovery_starts_poll_monitors(tmp_path, monkeypatch):
    """Recovery starts poll monitors for incomplete jobs."""
    monkeypatch.setattr("archon.research.store.RESEARCH_JOBS_DIR", tmp_path)
    monkeypatch.setattr("archon.research.store._RESEARCH_MONITORS", {})
    monkeypatch.setattr("archon.research.store._RESEARCH_RECOVERY_STARTED", False, raising=False)

    save_research_job(
        ResearchJobRecord(
            interaction_id="recover-123",
            status="in_progress",
            prompt="test query",
            agent="deep-research-pro-preview-12-2025",
            created_at="2099-01-01T00:00:00Z",
            updated_at="2099-01-01T00:00:00Z",
            summary="Research in progress",
            output_text="",
            error="",
            provider_status="in_progress",
            stream_status="started",
            poll_count=1,
            timeout_minutes=60,
        )
    )

    monitor_calls = []

    def _fake_start_monitor(interaction_id: str, *, refresh_client, poll_interval_sec=10, hook_bus=None):
        monitor_calls.append(interaction_id)
        return True

    monkeypatch.setattr("archon.research.store.start_research_job_monitor", _fake_start_monitor)

    class _Client:
        def get_research(self, interaction_id):
            return type("_I", (), {"status": "in_progress", "output_text": ""})()

    started = ensure_research_recovery_started(cfg=Config(), hook_bus=None, client=_Client())

    assert started is True
    assert monitor_calls == ["recover-123"]


def test_ensure_research_recovery_skips_terminal_jobs(tmp_path, monkeypatch):
    """Recovery ignores already-completed jobs."""
    monkeypatch.setattr("archon.research.store.RESEARCH_JOBS_DIR", tmp_path)
    monkeypatch.setattr("archon.research.store._RESEARCH_MONITORS", {})
    monkeypatch.setattr("archon.research.store._RESEARCH_RECOVERY_STARTED", False, raising=False)

    save_research_job(
        ResearchJobRecord(
            interaction_id="done-456",
            status="completed",
            prompt="test query",
            agent="deep-research-pro-preview-12-2025",
            created_at="2099-01-01T00:00:00Z",
            updated_at="2099-01-01T00:00:00Z",
            summary="Done",
            output_text="Result",
            error="",
        )
    )

    monitor_calls = []

    def _fake_start_monitor(interaction_id: str, *, refresh_client, poll_interval_sec=10, hook_bus=None):
        monitor_calls.append(interaction_id)
        return True

    monkeypatch.setattr("archon.research.store.start_research_job_monitor", _fake_start_monitor)

    started = ensure_research_recovery_started(cfg=Config(), hook_bus=None, client=object())

    assert started is True
    assert monitor_calls == []


def test_consume_research_stream_emits_completed_event_on_terminal_transition(tmp_path, monkeypatch):
    monkeypatch.setattr("archon.research.store.RESEARCH_JOBS_DIR", tmp_path)

    save_research_job(
        ResearchJobRecord(
            interaction_id="stream-emit-123",
            status="in_progress",
            prompt="test query",
            agent="deep-research-pro-preview-12-2025",
            created_at="2099-01-01T00:00:00Z",
            updated_at="2099-01-01T00:00:00Z",
            summary="Research job started",
            output_text="",
            error="",
        )
    )

    class _Event:
        def __init__(self, event_type: str, **fields):
            self.event_type = event_type
            for key, value in fields.items():
                setattr(self, key, value)

    events = iter([
        _Event("interaction.complete", status="completed", text="Final answer"),
    ])

    captured = []

    import archon.research.store as _store_mod

    def _capture_emit(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(_store_mod, "_emit_job_completed_event", _capture_emit)

    result = consume_research_stream("stream-emit-123", events, hook_bus="fake-bus")
    assert result is not None
    assert result.status == "completed"
    assert len(captured) == 1
    assert captured[0]["status"] == "completed"
    assert captured[0]["hook_bus"] == "fake-bus"


def test_consume_research_stream_skips_event_for_already_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr("archon.research.store.RESEARCH_JOBS_DIR", tmp_path)

    save_research_job(
        ResearchJobRecord(
            interaction_id="stream-already-done",
            status="completed",
            prompt="test query",
            agent="deep-research-pro-preview-12-2025",
            created_at="2099-01-01T00:00:00Z",
            updated_at="2099-01-01T00:00:00Z",
            summary="Already done",
            output_text="Old answer",
            error="",
        )
    )

    captured = []

    import archon.research.store as _store_mod

    def _capture_emit(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(_store_mod, "_emit_job_completed_event", _capture_emit)

    result = consume_research_stream(
        "stream-already-done", iter(()), hook_bus="fake-bus", mark_unfinished_as_error=False,
    )
    assert result is not None
    assert len(captured) == 0


def test_save_research_stream_error_emits_completed_event(tmp_path, monkeypatch):
    monkeypatch.setattr("archon.research.store.RESEARCH_JOBS_DIR", tmp_path)

    record = ResearchJobRecord(
        interaction_id="err-emit-123",
        status="in_progress",
        prompt="test query",
        agent="deep-research-pro-preview-12-2025",
        created_at="2099-01-01T00:00:00Z",
        updated_at="2099-01-01T00:00:00Z",
        summary="Research in progress",
        output_text="",
        error="",
    )
    save_research_job(record)

    captured = []

    import archon.research.store as _store_mod

    def _capture_emit(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(_store_mod, "_emit_job_completed_event", _capture_emit)

    result = _store_mod._save_research_stream_error(
        record, "Something broke", hook_bus="fake-bus",
    )
    assert result.status == "error"
    assert len(captured) == 1
    assert captured[0]["status"] == "error"
    assert captured[0]["hook_bus"] == "fake-bus"
