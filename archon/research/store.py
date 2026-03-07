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


def load_research_job(
    interaction_id: str,
    *,
    refresh_client=None,
) -> ResearchJobRecord | None:
    path = research_job_path(interaction_id)
    if not path.exists():
        return None
    data = _read_json_object(path)
    if data is None:
        return None
    record = ResearchJobRecord.from_dict(data)
    return _refresh_research_job(record, refresh_client=refresh_client)


def list_research_jobs(limit: int = 20, *, refresh_client=None) -> list[ResearchJobRecord]:
    _ensure_dirs()
    jobs: list[ResearchJobRecord] = []
    files = sorted(RESEARCH_JOBS_DIR.glob("*.json"), reverse=True)
    for path in files[: max(0, int(limit))]:
        data = _read_json_object(path)
        if data is None:
            continue
        jobs.append(
            _refresh_research_job(
                ResearchJobRecord.from_dict(data),
                refresh_client=refresh_client,
            )
        )
    return jobs


def cancel_research_job(interaction_id: str, reason: str = "Cancelled by user") -> ResearchJobRecord | None:
    """Cancel an in-progress research job by updating its status."""
    record = load_research_job(interaction_id)
    if record is None:
        return None
    if record.status not in ("in_progress", "running", "pending"):
        return record  # Already terminal
    record.status = "cancelled"
    record.error = reason
    record.updated_at = _now_iso()
    save_research_job(record)
    return record


def load_research_job_summary(interaction_id: str, *, refresh_client=None) -> JobSummary | None:
    record = load_research_job(interaction_id, refresh_client=refresh_client)
    if record is None:
        return None
    return summarize_research_job(record)


def list_research_job_summaries(limit: int = 20, *, refresh_client=None) -> list[JobSummary]:
    return [
        summarize_research_job(record)
        for record in list_research_jobs(limit=limit, refresh_client=refresh_client)
    ]


def poll_research_job(interaction_id: str) -> ResearchJobRecord | None:
    """Poll Google API for latest status and update stored record."""
    record = load_research_job(interaction_id)
    if record is None or record.status in ("completed", "cancelled", "error"):
        return record  # Terminal states don't need polling
    # Try polling via the API
    try:
        from archon.research.google_deep_research import GoogleDeepResearchClient
        import os

        api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            return record
        client = GoogleDeepResearchClient.from_api_key(api_key)
        return _refresh_research_job(record, refresh_client=client)
    except Exception:
        pass  # Polling failure is non-fatal
    return record


def purge_completed_jobs(statuses: list[str] | None = None) -> int:
    """Remove research jobs with given statuses. Returns count removed."""
    if statuses is None:
        statuses = ["cancelled", "error"]
    removed = 0
    if not RESEARCH_JOBS_DIR.exists():
        return 0
    for f in RESEARCH_JOBS_DIR.glob("*.json"):
        try:
            record = load_research_job(f.stem)
            if record and record.status in statuses:
                f.unlink()
                removed += 1
        except Exception:
            continue
    return removed


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


def _refresh_research_job(record: ResearchJobRecord, *, refresh_client=None) -> ResearchJobRecord:
    if refresh_client is None:
        return record
    try:
        interaction = refresh_client.get_research(record.interaction_id)
    except Exception:
        return record

    status = str(getattr(interaction, "status", "") or record.status or "unknown").strip()
    output_text = str(getattr(interaction, "output_text", "") or record.output_text or "").strip()
    summary = _summarize_research_state(status=status, output_text=output_text, fallback=record.summary)
    changed = (
        status != record.status
        or output_text != record.output_text
        or summary != record.summary
    )
    updated = ResearchJobRecord(
        interaction_id=record.interaction_id,
        status=status,
        prompt=record.prompt,
        agent=record.agent,
        created_at=record.created_at,
        updated_at=_now_iso() if changed else record.updated_at,
        summary=summary,
        output_text=output_text,
        error=record.error,
    )
    if changed:
        return save_research_job(updated)
    return updated


def _summarize_research_state(*, status: str, output_text: str, fallback: str) -> str:
    if output_text:
        first_line = output_text.splitlines()[0].strip()
        if first_line:
            return first_line
    normalized = str(status or "").strip().lower()
    if normalized in {"completed", "done"}:
        return "Research job completed"
    if normalized in {"running", "queued", "starting", "in_progress"} and fallback:
        return fallback
    return fallback or normalized or "unknown"
