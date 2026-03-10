"""Tests for research job cancellation and polling."""

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
    assert record.poll_count == 2


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


def test_load_research_job_marks_stale_stream_without_live_monitor_as_error(tmp_path, monkeypatch):
    monkeypatch.setattr("archon.research.store.RESEARCH_JOBS_DIR", tmp_path)

    save_research_job(
        ResearchJobRecord(
            interaction_id="stream-123",
            status="in_progress",
            prompt="test query",
            agent="deep-research-pro-preview-12-2025",
            created_at="2000-01-01T00:00:00Z",
            updated_at="2000-01-01T00:00:10Z",
            summary="Research job started",
            output_text="",
            error="",
            provider_status="in_progress",
            last_event_at="2000-01-01T00:00:10Z",
            stream_status="interaction.status_update",
            poll_count=2,
        )
    )

    record = load_research_job("stream-123")

    assert record is not None
    assert record.status == "error"
    assert record.summary == "Research stream inactive"
    assert "No active stream consumer" in record.error


def test_start_research_stream_job_starts_stream_in_worker_and_persists_updates(tmp_path, monkeypatch):
    monkeypatch.setattr("archon.research.store.RESEARCH_JOBS_DIR", tmp_path)

    class _Stream:
        interaction_id = "stream-job-123"
        status = "in_progress"

        def __init__(self):
            class _Event:
                def __init__(self, event_type: str, **fields):
                    self.event_type = event_type
                    for key, value in fields.items():
                        setattr(self, key, value)

            self.events = iter(
                [
                    _Event("content.delta", delta_type="thought_summary", text="Checking sources"),
                    _Event("interaction.complete", status="completed", text="Final answer"),
                ]
            )

    class _Client:
        def __init__(self):
            self.prompts = []

        def start_research_stream(self, prompt: str):
            self.prompts.append(prompt)
            return _Stream()

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

    reloaded = None
    for _ in range(20):
        reloaded = load_research_job("stream-job-123")
        if reloaded is not None and reloaded.status == "completed":
            break
        time.sleep(0.01)

    assert reloaded is not None
    assert reloaded.status == "completed"
    assert reloaded.output_text == "Final answer"


def test_start_research_stream_job_resumes_after_nonterminal_stream_end(tmp_path, monkeypatch):
    monkeypatch.setattr("archon.research.store.RESEARCH_JOBS_DIR", tmp_path)

    class _Stream:
        def __init__(self, interaction_id: str, events):
            self.interaction_id = interaction_id
            self.status = "in_progress"
            self.events = iter(events)

    class _Event:
        def __init__(self, event_type: str, **fields):
            self.event_type = event_type
            for key, value in fields.items():
                setattr(self, key, value)

    class _Client:
        def __init__(self):
            self.resume_calls = []

        def start_research_stream(self, prompt: str):
            return _Stream(
                "stream-job-456",
                [
                    _Event(
                        "content.delta",
                        delta_type="thought_summary",
                        text="Checking sources",
                        event_id="evt-1",
                    ),
                ],
            )

        def resume_research_stream(self, interaction_id: str, *, last_event_id: str):
            self.resume_calls.append((interaction_id, last_event_id))
            return _Stream(
                interaction_id,
                [
                    _Event(
                        "interaction.complete",
                        status="completed",
                        text="Final answer",
                        event_id="evt-2",
                    ),
                ],
            )

    client = _Client()

    record = start_research_stream_job(
        "test query",
        client=client,
        agent_name="deep-research-pro-preview-12-2025",
        timeout_minutes=20,
    )

    assert record is not None
    assert record.interaction_id == "stream-job-456"

    reloaded = None
    for _ in range(20):
        reloaded = load_research_job("stream-job-456")
        if reloaded is not None and reloaded.status == "completed":
            break
        time.sleep(0.01)

    assert client.resume_calls == [("stream-job-456", "evt-1")]
    assert reloaded is not None
    assert reloaded.status == "completed"
    assert reloaded.output_text == "Final answer"


def test_ensure_research_recovery_started_resumes_nonterminal_stream_job(tmp_path, monkeypatch):
    monkeypatch.setattr("archon.research.store.RESEARCH_JOBS_DIR", tmp_path)
    monkeypatch.setattr("archon.research.store._RESEARCH_MONITORS", {})
    monkeypatch.setattr("archon.research.store._RESEARCH_RECOVERY_STARTED", False, raising=False)

    save_research_job(
        ResearchJobRecord(
            interaction_id="resume-123",
            status="in_progress",
            prompt="test query",
            agent="deep-research-pro-preview-12-2025",
            created_at="2099-01-01T00:00:00Z",
            updated_at="2099-01-01T00:00:00Z",
            summary="Research in progress",
            output_text="",
            error="",
            provider_status="in_progress",
            last_event_at="2099-01-01T00:00:00Z",
            last_event_id="evt-1",
            stream_status="interaction.status_update",
            poll_count=1,
            timeout_minutes=60,
        )
    )

    class _Event:
        def __init__(self, event_type: str, **fields):
            self.event_type = event_type
            for key, value in fields.items():
                setattr(self, key, value)

    class _Stream:
        def __init__(self, interaction_id: str):
            self.interaction_id = interaction_id
            self.status = "in_progress"
            self.events = iter(
                [
                    _Event(
                        "interaction.complete",
                        status="completed",
                        text="Recovered final answer",
                        event_id="evt-2",
                    )
                ]
            )

    class _Client:
        def __init__(self):
            self.resume_calls = []

        def resume_research_stream(self, interaction_id: str, *, last_event_id: str):
            self.resume_calls.append((interaction_id, last_event_id))
            return _Stream(interaction_id)

    client = _Client()
    started = ensure_research_recovery_started(cfg=Config(), hook_bus=None, client=client)

    assert started is True
    reloaded = None
    for _ in range(20):
        reloaded = load_research_job("resume-123")
        if reloaded is not None and reloaded.status == "completed":
            break
        time.sleep(0.01)

    assert client.resume_calls == [("resume-123", "evt-1")]
    assert reloaded is not None
    assert reloaded.status == "completed"
    assert reloaded.output_text == "Recovered final answer"


def test_ensure_research_recovery_started_marks_missing_last_event_id_as_error(tmp_path, monkeypatch):
    monkeypatch.setattr("archon.research.store.RESEARCH_JOBS_DIR", tmp_path)
    monkeypatch.setattr("archon.research.store._RESEARCH_MONITORS", {})
    monkeypatch.setattr("archon.research.store._RESEARCH_RECOVERY_STARTED", False, raising=False)

    save_research_job(
        ResearchJobRecord(
            interaction_id="resume-456",
            status="in_progress",
            prompt="test query",
            agent="deep-research-pro-preview-12-2025",
            created_at="2099-01-01T00:00:00Z",
            updated_at="2099-01-01T00:00:00Z",
            summary="Research in progress",
            output_text="",
            error="",
            provider_status="in_progress",
            last_event_at="2099-01-01T00:00:00Z",
            stream_status="interaction.status_update",
            poll_count=1,
            timeout_minutes=60,
        )
    )

    started = ensure_research_recovery_started(cfg=Config(), hook_bus=None, client=object())

    assert started is True
    reloaded = load_research_job("resume-456")
    assert reloaded is not None
    assert reloaded.status == "error"
    assert reloaded.summary == "Research recovery unavailable"
    assert "Missing last_event_id" in reloaded.error
