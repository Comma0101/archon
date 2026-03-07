"""Lightweight shared job summaries for worker sessions and call missions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from archon.calls.models import CallMission
    from archon.research.models import ResearchJobRecord
    from archon.workers.session_store_models import WorkerSessionRecord


@dataclass(frozen=True)
class JobSummary:
    job_id: str
    kind: str
    status: str
    summary: str
    last_update_at: str

    def to_dict(self) -> dict[str, str]:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "status": self.status,
            "summary": self.summary,
            "last_update_at": self.last_update_at,
        }


def job_summary_from_worker_record(record: "WorkerSessionRecord") -> JobSummary:
    return JobSummary(
        job_id=f"worker:{record.session_id}",
        kind="worker_session",
        status=str(record.status or "").strip(),
        summary=_first_non_empty(record.summary, record.error, record.task, record.status),
        last_update_at=_first_non_empty(record.updated_at, record.completed_at, record.created_at),
    )


def job_summary_from_call_mission(mission: "CallMission") -> JobSummary:
    return JobSummary(
        job_id=f"call:{mission.call_session_id}",
        kind="call_mission",
        status=str(mission.status or "").strip(),
        summary=_first_non_empty(
            mission.evaluation_summary,
            mission.transcript_summary,
            mission.error,
            mission.goal,
            mission.status,
        ),
        last_update_at=_timestamp_to_iso(mission.updated_at or mission.created_at),
    )


def job_summary_from_research_record(record: "ResearchJobRecord") -> JobSummary:
    return JobSummary(
        job_id=f"research:{record.interaction_id}",
        kind="deep_research",
        status=str(record.status or "").strip(),
        summary=_first_non_empty(
            record.summary,
            record.output_text,
            record.error,
            record.prompt,
            record.status,
        ),
        last_update_at=_first_non_empty(record.updated_at, record.created_at),
    )


def format_job_summary(job: JobSummary) -> str:
    return "\n".join(
        [
            f"job_id: {job.job_id}",
            f"job_kind: {job.kind}",
            f"job_status: {job.status}",
            f"job_summary: {job.summary}",
            f"job_last_update_at: {job.last_update_at}",
        ]
    )


def format_job_summary_list(jobs: list[JobSummary]) -> str:
    if not jobs:
        return "(none)"
    lines: list[str] = []
    for job in jobs:
        lines.append(
            f"- {job.job_id} [{job.kind}] {job.status} | {job.last_update_at} | {job.summary}"
        )
    return "\n".join(lines)


def job_summary_from_dict(data: dict[str, object]) -> JobSummary:
    return JobSummary(
        job_id=str(data.get("job_id", "")),
        kind=str(data.get("kind", "")),
        status=str(data.get("status", "")),
        summary=str(data.get("summary", "")),
        last_update_at=str(data.get("last_update_at", "")),
    )


def _first_non_empty(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _timestamp_to_iso(value: object) -> str:
    try:
        timestamp = float(value or 0)
    except (TypeError, ValueError):
        return ""
    if timestamp <= 0:
        return ""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


summarize_worker_session = job_summary_from_worker_record
summarize_call_mission = job_summary_from_call_mission
summarize_research_job = job_summary_from_research_record
