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
            created_at="2026-03-06T22:00:00Z",
            updated_at="2026-03-06T22:05:00Z",
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
            created_at="2026-03-06T22:00:00Z",
            updated_at="2026-03-06T22:05:00Z",
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
    assert record.summary == "Research in progress"


def test_load_research_job_emits_progress_event_on_nonterminal_refresh(tmp_path, monkeypatch):
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
            summary="Research job started",
            output_text="",
            error="",
        )
    )

    events = []
    hook_bus = HookBus()
    hook_bus.register("ux.job_progress", lambda event: events.append(event))
    monkeypatch.setattr(research_store._emit_job_progress_event, "_hook_bus", hook_bus, raising=False)

    class _RefreshClient:
        def get_research(self, interaction_id: str):
            return SimpleNamespace(
                interaction_id=interaction_id,
                status="in_progress",
                output_text="",
            )

    record = load_research_job("abc", refresh_client=_RefreshClient())

    assert record is not None
    assert events
    payload = events[0].payload
    rendered = payload["event"].render_text()
    assert "research:abc" in rendered
    assert "in progress" in rendered.lower()
