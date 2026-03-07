"""Persistent storage for native research jobs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from archon.config import STATE_DIR
from archon.control.jobs import JobSummary, summarize_research_job
from archon.research.models import ResearchJobRecord


RESEARCH_STATE_DIR = STATE_DIR / "research"
RESEARCH_JOBS_DIR = RESEARCH_STATE_DIR / "jobs"


def save_research_job(record: ResearchJobRecord) -> ResearchJobRecord:
    _ensure_dirs()
    payload = record.to_dict()
    now = _now_iso()
    if not payload["created_at"]:
        payload["created_at"] = now
    if not payload["updated_at"]:
        payload["updated_at"] = now
    path = research_job_path(payload["interaction_id"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    return ResearchJobRecord.from_dict(payload)


def load_research_job(interaction_id: str) -> ResearchJobRecord | None:
    path = research_job_path(interaction_id)
    if not path.exists():
        return None
    data = _read_json_object(path)
    if data is None:
        return None
    return ResearchJobRecord.from_dict(data)


def list_research_jobs(limit: int = 20) -> list[ResearchJobRecord]:
    _ensure_dirs()
    jobs: list[ResearchJobRecord] = []
    files = sorted(RESEARCH_JOBS_DIR.glob("*.json"), reverse=True)
    for path in files[: max(0, int(limit))]:
        data = _read_json_object(path)
        if data is None:
            continue
        jobs.append(ResearchJobRecord.from_dict(data))
    return jobs


def load_research_job_summary(interaction_id: str) -> JobSummary | None:
    record = load_research_job(interaction_id)
    if record is None:
        return None
    return summarize_research_job(record)


def list_research_job_summaries(limit: int = 20) -> list[JobSummary]:
    return [summarize_research_job(record) for record in list_research_jobs(limit=limit)]


def research_job_path(interaction_id: str) -> Path:
    return RESEARCH_JOBS_DIR / f"{interaction_id}.json"


def _ensure_dirs() -> None:
    RESEARCH_JOBS_DIR.mkdir(parents=True, exist_ok=True)


def _read_json_object(path: Path) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
