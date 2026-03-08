"""Tests for research job cancellation and polling."""

import json
from pathlib import Path

from archon.config import Config
from archon.research.models import ResearchJobRecord
from archon.research.store import (
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

    def _fake_from_api_key(api_key: str, *, agent: str = ""):
        captured["api_key"] = api_key
        captured["agent"] = agent
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
    }
