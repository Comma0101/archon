"""Tests for research job cancellation."""

import json
from pathlib import Path

from archon.research.models import ResearchJobRecord
from archon.research.store import save_research_job, cancel_research_job, load_research_job


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
