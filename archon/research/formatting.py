"""Shared formatting helpers for Deep Research job state."""

from __future__ import annotations

from datetime import datetime, timezone

_RECENT_PROGRESS_WINDOW_SEC = 60


def format_research_job_record(record, *, cfg=None) -> str:
    interaction_id = str(getattr(record, "interaction_id", "") or "").strip()
    status = str(getattr(record, "status", "") or "unknown").strip() or "unknown"
    summary = str(getattr(record, "summary", "") or "").strip() or "unknown"
    updated_at = str(getattr(record, "updated_at", "") or "").strip()
    provider_status = str(getattr(record, "provider_status", "") or status).strip() or status
    last_polled_at = str(getattr(record, "last_polled_at", "") or "").strip() or "(not yet refreshed)"
    last_event_at = str(getattr(record, "last_event_at", "") or "").strip() or "(not yet streamed)"
    stream_status = str(getattr(record, "stream_status", "") or "").strip() or "(not yet started)"
    latest_thought_summary = str(getattr(record, "latest_thought_summary", "") or "").strip()
    event_count = max(0, int(getattr(record, "event_count", 0) or 0))
    poll_count = max(0, int(getattr(record, "poll_count", 0) or 0))
    created_at = str(getattr(record, "created_at", "") or "").strip()
    created_at_dt = _parse_iso_datetime(created_at)
    last_event_dt = _parse_iso_datetime(last_event_at if last_event_at != "(not yet streamed)" else "")
    timeout_minutes = max(
        1,
        int(
            getattr(record, "timeout_minutes", 0)
            or getattr(
                getattr(getattr(cfg, "research", None), "google_deep_research", None),
                "timeout_minutes",
                20,
            )
            or 20
        ),
    )
    lines = [
        f"job_id: research:{interaction_id}",
        "job_kind: deep_research",
        f"job_status: {status}",
        f"job_summary: {summary}",
        f"job_last_update_at: {updated_at}",
        f"job_provider_status: {provider_status}",
        f"job_last_polled_at: {last_polled_at}",
        f"job_last_event_at: {last_event_at}",
        f"job_stream_status: {stream_status}",
        f"job_elapsed: {_format_elapsed(created_at)}",
        f"job_event_count: {event_count}",
        f"job_poll_count: {poll_count}",
        f"job_live_status: {_format_research_live_status(status, stream_status, last_event_dt, created_at_dt, timeout_minutes)}",
        f"job_stream_age: {_format_refresh_age(last_event_dt)}",
    ]
    if latest_thought_summary:
        lines.append(f"job_latest_thought_summary: {latest_thought_summary}")
    output_text = str(getattr(record, "output_text", "") or "").strip()
    if output_text:
        lines.append("job_output_preview:")
        lines.extend(output_text[:1000].splitlines()[:10] or [output_text[:1000]])
    error = str(getattr(record, "error", "") or "").strip()
    if error:
        lines.append(f"job_error: {error}")
    return "\n".join(lines)


def format_research_job_compact_line(record, *, cfg=None) -> str:
    interaction_id = str(getattr(record, "interaction_id", "") or "").strip()
    status = str(getattr(record, "status", "") or "unknown").strip() or "unknown"
    provider_status = str(getattr(record, "provider_status", "") or status).strip() or status
    event_count = max(0, int(getattr(record, "event_count", 0) or 0))
    summary = str(getattr(record, "summary", "") or "").strip()
    if not summary:
        summary = str(getattr(record, "prompt", "") or "").strip()
    if not summary:
        summary = "No summary"
    return (
        f"research:{interaction_id} | {status} | provider={provider_status} | "
        f"events={event_count} | {summary}"
    )


def research_status_terminal(status: str) -> bool:
    return str(status or "").strip().lower() in {"completed", "done", "failed", "error", "cancelled"}


def _format_elapsed(started_at: str) -> str:
    started = _parse_iso_datetime(started_at)
    if started is None:
        return "unknown"
    delta = max(0, int((datetime.now(timezone.utc) - started).total_seconds()))
    if delta < 60:
        return f"{delta}s"
    if delta < 3600:
        minutes, seconds = divmod(delta, 60)
        return f"{minutes}m {seconds}s"
    hours, rem = divmod(delta, 3600)
    minutes = rem // 60
    return f"{hours}h {minutes}m"


def _format_refresh_age(last_polled_at: datetime | None) -> str:
    if last_polled_at is None:
        return "(not yet polled)"
    delta = max(0, int((datetime.now(timezone.utc) - last_polled_at).total_seconds()))
    if delta < 60:
        return f"{delta}s"
    if delta < 3600:
        minutes, seconds = divmod(delta, 60)
        return f"{minutes}m {seconds}s"
    hours, rem = divmod(delta, 3600)
    minutes = rem // 60
    return f"{hours}h {minutes}m"


def _format_research_live_status(
    status: str,
    stream_status: str,
    last_event_at: datetime | None,
    created_at: datetime | None,
    timeout_minutes: int,
) -> str:
    normalized = str(status or "").strip().lower()
    normalized_stream_status = str(stream_status or "").strip().lower()
    if normalized in {"in_progress", "running", "queued", "starting"}:
        if _research_runtime_exceeds_timeout(created_at, timeout_minutes):
            return f"stream active | running longer than configured {timeout_minutes}m timeout"
        if last_event_at is not None:
            age_seconds = max(0, int((datetime.now(timezone.utc) - last_event_at).total_seconds()))
            if age_seconds <= _RECENT_PROGRESS_WINDOW_SEC:
                return "stream active | recent progress"
            return "stream active | waiting for next progress"
        return "stream started | waiting for first event"
    if normalized == "requires_action":
        return "stream active | action required"
    if research_status_terminal(normalized):
        return f"terminal | {stream_status or normalized}"
    return f"stream state | {stream_status or normalized or 'unknown'}"


def _research_runtime_exceeds_timeout(created_at: datetime | None, timeout_minutes: int) -> bool:
    if created_at is None:
        return False
    elapsed_seconds = max(0, int((datetime.now(timezone.utc) - created_at).total_seconds()))
    return elapsed_seconds > max(1, int(timeout_minutes or 20)) * 60


def _parse_iso_datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None
