"""Tests for native research clients and stores."""

import warnings
from types import SimpleNamespace

import pytest

from archon.control.hooks import HookBus
from archon.research import store as research_store
from archon.research.google_deep_research import GoogleDeepResearchClient
from archon.research.models import ResearchJobRecord
from archon.research.store import (
    load_research_job,
    load_research_job_summary,
    save_research_job,
)


class _FakeInteractionsClient:
    def __init__(self):
        self.create_calls = []
        self.get_calls = []
        self.cancel_calls = []

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return {
            "id": "int-123",
            "status": "running",
            "response": None,
        }

    def get(self, interaction_id: str):
        self.get_calls.append(interaction_id)
        return {
            "id": interaction_id,
            "status": "completed",
            "response": {
                "output_text": "done",
            },
        }

    def cancel(self, interaction_id: str):
        self.cancel_calls.append(interaction_id)
        return {
            "id": interaction_id,
            "status": "cancelled",
            "response": None,
        }


def test_google_deep_research_client_starts_background_interaction():
    fake = _FakeInteractionsClient()
    client = GoogleDeepResearchClient(fake, agent="deep-research-pro-preview-12-2025")

    result = client.start_research("Research LA restaurant market")

    assert result.interaction_id == "int-123"
    assert result.status == "running"
    assert fake.create_calls == [
        {
            "agent": "deep-research-pro-preview-12-2025",
            "input": "Research LA restaurant market",
            "background": True,
            "store": True,
            "agent_config": {
                "type": "deep-research",
                "thinking_summaries": "auto",
            },
            "tools": None,
        }
    ]


def test_google_deep_research_client_loads_interaction_status():
    fake = _FakeInteractionsClient()
    client = GoogleDeepResearchClient(fake, agent="deep-research-pro-preview-12-2025")

    result = client.get_research("int-123")

    assert result.interaction_id == "int-123"
    assert result.status == "completed"
    assert result.output_text == "done"
    assert fake.get_calls == ["int-123"]


def test_google_deep_research_client_cancels_interaction():
    fake = _FakeInteractionsClient()
    client = GoogleDeepResearchClient(fake, agent="deep-research-pro-preview-12-2025")

    result = client.cancel_research("int-123")

    assert result.interaction_id == "int-123"
    assert result.status == "cancelled"
    assert fake.cancel_calls == ["int-123"]


def test_google_deep_research_client_reads_output_text_from_outputs_list():
    class _DocShapeInteractionsClient:
        def create(self, **kwargs):
            return {"id": "unused", "status": "running"}

        def get(self, interaction_id: str):
            return {
                "id": interaction_id,
                "status": "completed",
                "outputs": [
                    {"text": "final report body"},
                ],
            }

    client = GoogleDeepResearchClient(
        _DocShapeInteractionsClient(),
        agent="deep-research-pro-preview-12-2025",
    )

    result = client.get_research("int-123")

    assert result.output_text == "final report body"


def test_google_deep_research_client_suppresses_interactions_experimental_warning():
    class _WarningInteractionsClient:
        @property
        def interactions(self):
            warnings.warn(
                "Interactions usage is experimental and may change in future versions.",
                UserWarning,
            )
            return self

        def create(self, **kwargs):
            return {"id": "int-123", "status": "running"}

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        client = GoogleDeepResearchClient(
            _WarningInteractionsClient(),
            agent="deep-research-pro-preview-12-2025",
        )
        client.start_research("Research LA restaurant market")

    assert caught == []


def test_google_deep_research_client_rejects_custom_tools():
    fake = _FakeInteractionsClient()
    client = GoogleDeepResearchClient(fake, agent="deep-research-pro-preview-12-2025")

    with pytest.raises(ValueError, match="only supports built-in web research and optional file_search"):
        client.start_research(
            "Research LA restaurant market",
            tools=[{"type": "mcp", "server": "exa"}],
        )


def test_google_deep_research_client_starts_streaming_interaction():
    class _StreamInteractionsClient:
        def __init__(self):
            self.create_calls = []

        def create(self, **kwargs):
            self.create_calls.append(kwargs)
            return iter(
                [
                    {
                        "event_type": "interaction.start",
                        "interaction": {"id": "int-stream-123", "status": "in_progress"},
                    },
                    {
                        "event_type": "content.delta",
                        "delta": {"type": "thought_summary", "text": "Looking at sources"},
                    },
                    {
                        "event_type": "interaction.complete",
                        "interaction": {"id": "int-stream-123", "status": "completed"},
                    },
                ]
            )

    fake = _StreamInteractionsClient()
    client = GoogleDeepResearchClient(fake, agent="deep-research-pro-preview-12-2025")

    stream = client.start_research_stream("Research LA restaurant market")

    assert stream.interaction_id == "int-stream-123"
    events = list(stream.events)
    assert [event.event_type for event in events] == [
        "interaction.start",
        "content.delta",
        "interaction.complete",
    ]
    assert fake.create_calls == [
        {
            "agent": "deep-research-pro-preview-12-2025",
            "input": "Research LA restaurant market",
            "background": True,
            "store": True,
            "stream": True,
            "agent_config": {
                "type": "deep-research",
                "thinking_summaries": "auto",
            },
            "tools": None,
        }
    ]


def test_google_deep_research_client_stream_reads_nested_thought_summary_text():
    class _StreamInteractionsClient:
        def create(self, **kwargs):
            return iter(
                [
                    {
                        "event_type": "interaction.start",
                        "interaction": {"id": "int-stream-123", "status": "in_progress"},
                        "event_id": "evt-1",
                    },
                    {
                        "event_type": "content.delta",
                        "event_id": "evt-2",
                        "delta": {
                            "type": "thought_summary",
                            "content": {"text": "Looking at sources"},
                        },
                    },
                ]
            )

    client = GoogleDeepResearchClient(
        _StreamInteractionsClient(),
        agent="deep-research-pro-preview-12-2025",
    )

    stream = client.start_research_stream("Research LA restaurant market")
    events = list(stream.events)

    assert stream.last_event_id == "evt-1"
    assert events[1].delta_type == "thought_summary"
    assert events[1].text == "Looking at sources"
    assert events[1].event_id == "evt-2"


def test_google_deep_research_client_stream_reads_top_level_status_update_fields():
    class _StreamInteractionsClient:
        def create(self, **kwargs):
            return iter(
                [
                    {
                        "event_type": "interaction.start",
                        "interaction": {"id": "int-stream-123", "status": "in_progress"},
                        "event_id": "evt-1",
                    },
                    {
                        "event_type": "interaction.status_update",
                        "interaction_id": "int-stream-123",
                        "status": "in_progress",
                    },
                ]
            )

    client = GoogleDeepResearchClient(
        _StreamInteractionsClient(),
        agent="deep-research-pro-preview-12-2025",
    )

    stream = client.start_research_stream("Research LA restaurant market")
    events = list(stream.events)

    assert events[1].event_type == "interaction.status_update"
    assert events[1].interaction_id == "int-stream-123"
    assert events[1].status == "in_progress"


def test_google_deep_research_client_can_resume_stream_from_last_event_id():
    class _ResumeInteractionsClient:
        def __init__(self):
            self.get_calls = []

        def get(self, interaction_id: str, **kwargs):
            self.get_calls.append((interaction_id, kwargs))
            return iter(
                [
                    {
                        "event_type": "content.delta",
                        "event_id": "evt-3",
                        "delta": {"type": "text", "text": "Final answer"},
                    },
                    {
                        "event_type": "interaction.complete",
                        "interaction": {"id": interaction_id, "status": "completed"},
                        "event_id": "evt-4",
                    },
                ]
            )

    fake = _ResumeInteractionsClient()
    client = GoogleDeepResearchClient(fake, agent="deep-research-pro-preview-12-2025")

    stream = client.resume_research_stream("int-stream-123", last_event_id="evt-2")

    assert stream.last_event_id == "evt-3"
    assert [event.event_type for event in stream.events] == [
        "content.delta",
        "interaction.complete",
    ]
    assert fake.get_calls == [
        (
            "int-stream-123",
            {
                "stream": True,
                "last_event_id": "evt-2",
            },
        )
    ]


def test_google_deep_research_client_stream_reads_final_output_from_completion_payload():
    class _StreamInteractionsClient:
        def create(self, **kwargs):
            return iter(
                [
                    {
                        "event_type": "interaction.start",
                        "interaction": {"id": "int-stream-123", "status": "in_progress"},
                        "event_id": "evt-1",
                    },
                    {
                        "event_type": "interaction.complete",
                        "event_id": "evt-2",
                        "interaction": {
                            "id": "int-stream-123",
                            "status": "completed",
                            "outputs": [{"text": "Final report body"}],
                        },
                    },
                ]
            )

    client = GoogleDeepResearchClient(
        _StreamInteractionsClient(),
        agent="deep-research-pro-preview-12-2025",
    )

    stream = client.start_research_stream("Research LA restaurant market")
    events = list(stream.events)

    assert events[1].event_type == "interaction.complete"
    assert events[1].text == "Final report body"


def test_google_deep_research_client_debug_stream_tracing_emits_raw_and_normalized_markers(
    monkeypatch, capsys
):
    monkeypatch.setenv("ARCHON_DEEP_RESEARCH_DEBUG", "1")

    class _StreamInteractionsClient:
        def create(self, **kwargs):
            return iter(
                [
                    {
                        "event_type": "interaction.start",
                        "interaction": {"id": "int-stream-123", "status": "in_progress"},
                        "event_id": "evt-1",
                    },
                    {
                        "event_type": "content.delta",
                        "event_id": "evt-2",
                        "delta": {
                            "type": "thought_summary",
                            "content": {"text": "Looking at sources"},
                        },
                    },
                ]
            )

    client = GoogleDeepResearchClient(
        _StreamInteractionsClient(),
        agent="deep-research-pro-preview-12-2025",
    )

    stream = client.start_research_stream("Research LA restaurant market")
    list(stream.events)
    captured = capsys.readouterr()

    assert "[deep-research-debug]" in captured.err
    assert "type=content.delta" in captured.err
    assert "raw_event_id=yes" in captured.err
    assert "delta.content.text=yes" in captured.err
    assert "normalized_delta_type=thought_summary" in captured.err
    assert "normalized_text=yes" in captured.err


def test_google_deep_research_client_debug_stream_tracing_is_silent_by_default(monkeypatch, capsys):
    monkeypatch.delenv("ARCHON_DEEP_RESEARCH_DEBUG", raising=False)

    class _StreamInteractionsClient:
        def create(self, **kwargs):
            return iter(
                [
                    {
                        "event_type": "interaction.start",
                        "interaction": {"id": "int-stream-123", "status": "in_progress"},
                        "event_id": "evt-1",
                    },
                    {
                        "event_type": "content.delta",
                        "event_id": "evt-2",
                        "delta": {
                            "type": "thought_summary",
                            "content": {"text": "Looking at sources"},
                        },
                    },
                ]
            )

    client = GoogleDeepResearchClient(
        _StreamInteractionsClient(),
        agent="deep-research-pro-preview-12-2025",
    )

    stream = client.start_research_stream("Research LA restaurant market")
    list(stream.events)
    captured = capsys.readouterr()

    assert captured.err == ""


def test_research_job_summary_round_trips_from_store(tmp_path, monkeypatch):
    jobs_dir = tmp_path / "research" / "jobs"
    monkeypatch.setattr("archon.research.store.RESEARCH_JOBS_DIR", jobs_dir)

    save_research_job(
        ResearchJobRecord(
            interaction_id="abc",
            status="running",
            prompt="Research LA restaurant market",
            agent="deep-research-pro-preview-12-2025",
            created_at="2026-03-06T22:00:00Z",
            updated_at="2026-03-06T22:05:00Z",
            summary="LA market research started",
            output_text="",
            error="",
        )
    )

    summary = load_research_job_summary("abc")

    assert summary is not None
    assert summary.job_id == "research:abc"
    assert summary.kind == "deep_research"
    assert summary.status == "running"
    assert summary.summary == "LA market research started"


def test_load_research_job_summary_refreshes_and_persists_latest_interaction_state(tmp_path, monkeypatch):
    jobs_dir = tmp_path / "research" / "jobs"
    monkeypatch.setattr("archon.research.store.RESEARCH_JOBS_DIR", jobs_dir)

    save_research_job(
        ResearchJobRecord(
            interaction_id="abc",
            status="running",
            prompt="Research LA restaurant market",
            agent="deep-research-pro-preview-12-2025",
            created_at="2099-03-08T07:50:00Z",
            updated_at="2099-03-08T07:55:00Z",
            summary="Research job started",
            output_text="",
            error="",
        )
    )

    class _RefreshClient:
        def __init__(self):
            self.calls = []

        def get_research(self, interaction_id: str):
            self.calls.append(interaction_id)
            return SimpleNamespace(
                interaction_id=interaction_id,
                status="completed",
                output_text="Final report body",
            )

    refresh_client = _RefreshClient()

    summary = load_research_job_summary("abc", refresh_client=refresh_client)

    assert refresh_client.calls == ["abc"]
    assert summary is not None
    assert summary.status == "completed"
    assert summary.summary == "Final report body"

    reloaded = load_research_job_summary("abc")

    assert reloaded is not None
    assert reloaded.status == "completed"
    assert reloaded.summary == "Final report body"


def test_load_research_job_refresh_tracks_poll_metadata_and_provider_status(tmp_path, monkeypatch):
    jobs_dir = tmp_path / "research" / "jobs"
    monkeypatch.setattr("archon.research.store.RESEARCH_JOBS_DIR", jobs_dir)

    save_research_job(
        ResearchJobRecord(
            interaction_id="abc",
            status="running",
            prompt="Research LA restaurant market",
            agent="deep-research-pro-preview-12-2025",
            created_at="2099-03-08T07:50:00Z",
            updated_at="2099-03-08T07:55:00Z",
            summary="Research job started",
            output_text="",
            error="",
        )
    )

    class _RefreshClient:
        def get_research(self, interaction_id: str):
            return SimpleNamespace(
                interaction_id=interaction_id,
                status="in_progress",
                output_text="",
            )

    record = load_research_job("abc", refresh_client=_RefreshClient())

    assert record is not None
    assert record.provider_status == "in_progress"
    assert record.last_polled_at
    assert record.poll_count == 1
    assert record.event_count == 0
    assert record.summary == "Research in progress"


def test_consume_research_stream_tracks_event_count_without_incrementing_poll_count(tmp_path, monkeypatch):
    jobs_dir = tmp_path / "research" / "jobs"
    monkeypatch.setattr("archon.research.store.RESEARCH_JOBS_DIR", jobs_dir)

    save_research_job(
        ResearchJobRecord(
            interaction_id="abc",
            status="running",
            prompt="Research LA restaurant market",
            agent="deep-research-pro-preview-12-2025",
            created_at="2099-03-08T07:50:00Z",
            updated_at="2099-03-08T07:55:00Z",
            summary="Research job started",
            output_text="",
            error="",
            poll_count=4,
        )
    )

    events = [
        SimpleNamespace(
            event_type="content.delta",
            event_id="evt-1",
            text="Checking sources",
            delta_type="thought_summary",
            status="in_progress",
        ),
        SimpleNamespace(
            event_type="interaction.complete",
            event_id="evt-2",
            text="Final report",
            delta_type="text",
            status="completed",
        ),
    ]

    record = research_store.consume_research_stream("abc", events, mark_unfinished_as_error=False)

    assert record is not None
    assert record.event_count == 2
    assert record.poll_count == 4
    assert record.last_event_id == "evt-2"
    assert record.status == "completed"


def test_consume_research_stream_emits_progress_event_for_new_thought_summary(tmp_path, monkeypatch):
    jobs_dir = tmp_path / "research" / "jobs"
    monkeypatch.setattr("archon.research.store.RESEARCH_JOBS_DIR", jobs_dir)

    save_research_job(
        ResearchJobRecord(
            interaction_id="abc",
            status="running",
            prompt="Research LA restaurant market",
            agent="deep-research-pro-preview-12-2025",
            created_at="2099-03-08T07:50:00Z",
            updated_at="2099-03-08T07:55:00Z",
            summary="Research job started",
            output_text="",
            error="",
        )
    )

    events = []
    hook_bus = HookBus()
    hook_bus.register("ux.job_progress", lambda event: events.append(event))

    record = research_store.consume_research_stream(
        "abc",
        [
            SimpleNamespace(
                event_type="content.delta",
                event_id="evt-1",
                text="Checking sources",
                delta_type="thought_summary",
                status="in_progress",
            )
        ],
        hook_bus=hook_bus,
        mark_unfinished_as_error=False,
    )

    assert record is not None
    assert len(events) == 1
    assert "research:abc" in events[0].payload["event"].render_text()
    assert "Checking sources" in events[0].payload["event"].render_text()


def test_consume_research_stream_deduplicates_repeated_thought_summary_events(tmp_path, monkeypatch):
    jobs_dir = tmp_path / "research" / "jobs"
    monkeypatch.setattr("archon.research.store.RESEARCH_JOBS_DIR", jobs_dir)

    save_research_job(
        ResearchJobRecord(
            interaction_id="abc",
            status="running",
            prompt="Research LA restaurant market",
            agent="deep-research-pro-preview-12-2025",
            created_at="2099-03-08T07:50:00Z",
            updated_at="2099-03-08T07:55:00Z",
            summary="Research job started",
            output_text="",
            error="",
        )
    )

    events = []
    hook_bus = HookBus()
    hook_bus.register("ux.job_progress", lambda event: events.append(event))

    record = research_store.consume_research_stream(
        "abc",
        [
            SimpleNamespace(
                event_type="content.delta",
                event_id="evt-1",
                text="Checking sources",
                delta_type="thought_summary",
                status="in_progress",
            ),
            SimpleNamespace(
                event_type="content.delta",
                event_id="evt-2",
                text="Checking sources",
                delta_type="thought_summary",
                status="in_progress",
            ),
        ],
        hook_bus=hook_bus,
        mark_unfinished_as_error=False,
    )

    assert record is not None
    assert record.latest_thought_summary == "Checking sources"
    assert len(events) == 1


def test_load_research_job_emits_progress_event_on_nonterminal_refresh(tmp_path, monkeypatch):
    jobs_dir = tmp_path / "research" / "jobs"
    monkeypatch.setattr("archon.research.store.RESEARCH_JOBS_DIR", jobs_dir)

    save_research_job(
        ResearchJobRecord(
            interaction_id="abc",
            status="running",
            prompt="Research LA restaurant market",
            agent="deep-research-pro-preview-12-2025",
            created_at="2099-03-08T07:50:00Z",
            updated_at="2099-03-08T07:55:00Z",
            summary="Research job started",
            output_text="",
            error="",
        )
    )

    events = []
    hook_bus = HookBus()
    hook_bus.register("ux.job_progress", lambda event: events.append(event))

    class _RefreshClient:
        def get_research(self, interaction_id: str):
            return SimpleNamespace(
                interaction_id=interaction_id,
                status="in_progress",
                output_text="",
            )

    record = load_research_job("abc", refresh_client=_RefreshClient(), hook_bus=hook_bus)

    assert record is not None
    assert events
    payload = events[0].payload
    rendered = payload["event"].render_text()
    assert "research:abc" in rendered
    assert "in progress" in rendered.lower()


def test_agent_init_does_not_mutate_global_research_hook_bus(monkeypatch):
    monkeypatch.delattr(research_store._emit_job_progress_event, "_hook_bus", raising=False)
    monkeypatch.delattr(research_store._emit_job_completed_event, "_hook_bus", raising=False)

    from archon.agent import Agent
    from archon.config import Config
    from archon.tools import ToolRegistry

    llm = SimpleNamespace()
    Agent(llm, ToolRegistry(archon_source_dir=None), Config())
    Agent(llm, ToolRegistry(archon_source_dir=None), Config())

    assert not hasattr(research_store._emit_job_progress_event, "_hook_bus")
    assert not hasattr(research_store._emit_job_completed_event, "_hook_bus")


def test_load_research_job_times_out_overdue_nonterminal_job_and_attempts_remote_cancel(tmp_path, monkeypatch):
    jobs_dir = tmp_path / "research" / "jobs"
    monkeypatch.setattr("archon.research.store.RESEARCH_JOBS_DIR", jobs_dir)

    save_research_job(
        ResearchJobRecord(
            interaction_id="abc",
            status="running",
            prompt="Research LA restaurant market",
            agent="deep-research-pro-preview-12-2025",
            created_at="2000-03-06T22:00:00Z",
            updated_at="2000-03-06T22:05:00Z",
            summary="Research job started",
            output_text="",
            error="",
            timeout_minutes=20,
        )
    )

    class _RefreshClient:
        def __init__(self):
            self.cancel_calls = []

        def get_research(self, interaction_id: str):
            return SimpleNamespace(
                interaction_id=interaction_id,
                status="in_progress",
                output_text="",
            )

        def cancel_research(self, interaction_id: str):
            self.cancel_calls.append(interaction_id)
            return SimpleNamespace(
                interaction_id=interaction_id,
                status="cancelled",
                output_text="",
            )

    refresh_client = _RefreshClient()

    record = load_research_job("abc", refresh_client=refresh_client)

    assert record is not None
    assert record.status == "error"
    assert record.summary == "Research job exceeded configured timeout (20m)"
    assert record.provider_status == "in_progress"
    assert "Timed out after 20m" in record.error
    assert refresh_client.cancel_calls == ["abc"]
