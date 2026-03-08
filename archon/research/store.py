"""Persistent storage for native research jobs."""

from __future__ import annotations

import json
import os
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from archon.config import STATE_DIR, load_config
from archon.control.jobs import JobSummary, summarize_research_job
from archon.research.google_deep_research import GoogleDeepResearchClient
from archon.research.models import ResearchJobRecord


RESEARCH_STATE_DIR = STATE_DIR / "research"
RESEARCH_JOBS_DIR = RESEARCH_STATE_DIR / "jobs"
_RESEARCH_MONITOR_LOCK = threading.Lock()
_RESEARCH_MONITORS: dict[str, threading.Thread] = {}
_STREAM_STALE_AFTER_SEC = 60


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
    hook_bus=None,
) -> ResearchJobRecord | None:
    path = research_job_path(interaction_id)
    if not path.exists():
        return None
    data = _read_json_object(path)
    if data is None:
        return None
    record = _reconcile_local_research_job(ResearchJobRecord.from_dict(data), hook_bus=hook_bus)
    if refresh_client is None:
        return _attach_refresh_meta(record, attempted=False, ok=False, error="")
    return _refresh_research_job(record, refresh_client=refresh_client, hook_bus=hook_bus)


def list_research_jobs(limit: int = 20, *, refresh_client=None, hook_bus=None) -> list[ResearchJobRecord]:
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
                hook_bus=hook_bus,
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
    record.summary = "Research job cancelled"
    record.provider_status = str(record.provider_status or "cancelled").strip() or "cancelled"
    record.error = reason
    record.updated_at = _now_iso()
    save_research_job(record)
    return record


def load_research_job_summary(interaction_id: str, *, refresh_client=None, hook_bus=None) -> JobSummary | None:
    record = load_research_job(interaction_id, refresh_client=refresh_client, hook_bus=hook_bus)
    if record is None:
        return None
    return summarize_research_job(record)


def list_research_job_summaries(limit: int = 20, *, refresh_client=None, hook_bus=None) -> list[JobSummary]:
    return [
        summarize_research_job(record)
        for record in list_research_jobs(limit=limit, refresh_client=refresh_client, hook_bus=hook_bus)
    ]


def poll_research_job(interaction_id: str) -> ResearchJobRecord | None:
    """Poll Google API for latest status and update stored record."""
    record = load_research_job(interaction_id)
    if record is None or record.status in ("completed", "cancelled", "error"):
        return record  # Terminal states don't need polling
    client = _make_research_refresh_client()
    if client is not None:
        try:
            return _refresh_research_job(record, refresh_client=client)
        except Exception:
            pass  # Polling failure is non-fatal
    return record


def consume_research_stream(interaction_id: str, events, *, hook_bus=None) -> ResearchJobRecord | None:
    record = load_research_job(interaction_id)
    if record is None:
        return None
    latest = record
    for event in events:
        now = _now_iso()
        event_type = str(getattr(event, "event_type", "") or "").strip().lower()
        text = str(getattr(event, "text", "") or "").strip()
        delta_type = str(getattr(event, "delta_type", "") or "").strip().lower()
        status = str(getattr(event, "status", "") or latest.status or "").strip().lower() or latest.status
        summary = latest.summary
        output_text = latest.output_text
        latest_thought_summary = latest.latest_thought_summary
        if delta_type == "thought_summary" and text:
            latest_thought_summary = text
            summary = text
        if event_type == "interaction.complete":
            status = "completed"
            if text:
                output_text = text
                summary = text
            elif latest.output_text:
                summary = latest.output_text
            else:
                summary = "Research job completed"
        latest = save_research_job(
            ResearchJobRecord(
                interaction_id=latest.interaction_id,
                status=status,
                prompt=latest.prompt,
                agent=latest.agent,
                created_at=latest.created_at,
                updated_at=now,
                summary=summary,
                output_text=output_text,
                error=latest.error,
                provider_status=status,
                last_polled_at=latest.last_polled_at,
                last_event_at=now,
                stream_status=event_type,
                latest_thought_summary=latest_thought_summary,
                poll_count=max(0, int(latest.poll_count or 0)) + 1,
                timeout_minutes=max(1, int(latest.timeout_minutes or 20)),
            )
        )
    if not _is_terminal_research_status(latest.status):
        latest = save_research_job(
            ResearchJobRecord(
                interaction_id=latest.interaction_id,
                status="error",
                prompt=latest.prompt,
                agent=latest.agent,
                created_at=latest.created_at,
                updated_at=_now_iso(),
                summary="Research stream ended before completion",
                output_text=latest.output_text,
                error="Research stream ended before completion",
                provider_status=latest.provider_status or latest.status,
                last_polled_at=latest.last_polled_at,
                last_event_at=latest.last_event_at,
                stream_status="stream.ended",
                latest_thought_summary=latest.latest_thought_summary,
                poll_count=max(0, int(latest.poll_count or 0)),
                timeout_minutes=max(1, int(latest.timeout_minutes or 20)),
            )
        )
        _emit_job_completed_event(
            job_kind="research",
            job_id=f"research:{latest.interaction_id}",
            status="error",
            summary=latest.summary,
            hook_bus=hook_bus,
        )
    return latest


def start_research_stream_job(
    prompt: str,
    *,
    client,
    agent_name: str,
    timeout_minutes: int,
    hook_bus=None,
    startup_timeout_sec: int = 15,
) -> ResearchJobRecord:
    startup_queue: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=1)

    def _worker() -> None:
        interaction_id = ""
        try:
            stream = client.start_research_stream(prompt)
            timestamp = _now_iso()
            interaction_id = str(getattr(stream, "interaction_id", "") or "").strip()
            status = str(getattr(stream, "status", "") or "running").strip() or "running"
            record = save_research_job(
                ResearchJobRecord(
                    interaction_id=interaction_id,
                    status=status,
                    prompt=prompt,
                    agent=agent_name,
                    created_at=timestamp,
                    updated_at=timestamp,
                    summary="Research job started",
                    output_text="",
                    error="",
                    provider_status=status,
                    stream_status="started",
                    timeout_minutes=max(1, int(timeout_minutes or 20)),
                )
            )
            with _RESEARCH_MONITOR_LOCK:
                _RESEARCH_MONITORS[interaction_id] = threading.current_thread()
            startup_queue.put(("ok", record))
            consume_research_stream(interaction_id, getattr(stream, "events", None), hook_bus=hook_bus)
        except Exception as e:
            if interaction_id:
                existing = load_research_job(interaction_id)
                if existing is not None and not _is_terminal_research_status(existing.status):
                    save_research_job(
                        ResearchJobRecord(
                            interaction_id=existing.interaction_id,
                            status="error",
                            prompt=existing.prompt,
                            agent=existing.agent,
                            created_at=existing.created_at,
                            updated_at=_now_iso(),
                            summary="Research stream failed",
                            output_text=existing.output_text,
                            error=f"{type(e).__name__}: {e}",
                            provider_status=existing.provider_status or existing.status,
                            last_polled_at=existing.last_polled_at,
                            last_event_at=existing.last_event_at,
                            stream_status="error",
                            latest_thought_summary=existing.latest_thought_summary,
                            poll_count=max(0, int(existing.poll_count or 0)),
                            timeout_minutes=max(1, int(existing.timeout_minutes or 20)),
                        )
                    )
            try:
                startup_queue.put(("error", e))
            except Exception:
                pass
        finally:
            if interaction_id:
                with _RESEARCH_MONITOR_LOCK:
                    current = _RESEARCH_MONITORS.get(interaction_id)
                    if current is threading.current_thread():
                        _RESEARCH_MONITORS.pop(interaction_id, None)

    thread = threading.Thread(
        target=_worker,
        name="archon-research-stream-start",
        daemon=True,
    )
    thread.start()
    kind, payload = startup_queue.get(timeout=max(1, int(startup_timeout_sec or 15)))
    if kind == "error":
        raise payload
    return payload


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


def start_research_job_monitor(
    interaction_id: str,
    *,
    refresh_client,
    poll_interval_sec: int = 10,
    hook_bus=None,
) -> bool:
    normalized_id = str(interaction_id or "").strip()
    if not normalized_id or refresh_client is None:
        return False
    interval = max(1, int(poll_interval_sec or 10))
    with _RESEARCH_MONITOR_LOCK:
        existing = _RESEARCH_MONITORS.get(normalized_id)
        if existing is not None and existing.is_alive():
            return False
        thread = threading.Thread(
            target=_monitor_research_job_loop,
            args=(normalized_id, refresh_client, interval, hook_bus),
            name=f"archon-research-{normalized_id[:24]}",
            daemon=True,
        )
        _RESEARCH_MONITORS[normalized_id] = thread
    thread.start()
    return True


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


def _make_research_refresh_client(cfg=None):
    if cfg is None:
        try:
            cfg = load_config()
        except Exception:
            cfg = None

    api_key = ""
    agent = ""
    if cfg is not None:
        deep_cfg = getattr(getattr(cfg, "research", None), "google_deep_research", None)
        if deep_cfg is None or not bool(getattr(deep_cfg, "enabled", False)):
            return None
        agent = str(getattr(deep_cfg, "agent", "") or "").strip()
        llm_cfg = getattr(cfg, "llm", None)
        if str(getattr(llm_cfg, "provider", "") or "").strip().lower() == "google":
            api_key = str(getattr(llm_cfg, "api_key", "") or "").strip()
        if not api_key and str(getattr(llm_cfg, "fallback_provider", "") or "").strip().lower() == "google":
            api_key = str(getattr(llm_cfg, "fallback_api_key", "") or "").strip()

    if not api_key:
        api_key = str(os.environ.get("GEMINI_API_KEY", "")).strip()
    if not api_key:
        api_key = str(os.environ.get("GOOGLE_API_KEY", "")).strip()
    if not api_key:
        return None

    try:
        kwargs = {"agent": agent} if agent else {}
        return GoogleDeepResearchClient.from_api_key(api_key, **kwargs)
    except Exception:
        return None


def _refresh_research_job(record: ResearchJobRecord, *, refresh_client=None, hook_bus=None) -> ResearchJobRecord:
    if _is_terminal_research_status(record.status):
        return _attach_refresh_meta(record, attempted=False, ok=False, error="")
    if refresh_client is None:
        return _attach_refresh_meta(record, attempted=False, ok=False, error="")
    try:
        interaction = refresh_client.get_research(record.interaction_id)
    except Exception as e:
        return _attach_refresh_meta(
            record,
            attempted=True,
            ok=False,
            error=f"{type(e).__name__}: {e}",
        )

    polled_at = _now_iso()
    status = str(getattr(interaction, "status", "") or record.status or "unknown").strip()
    output_text = str(getattr(interaction, "output_text", "") or record.output_text or "").strip()
    timeout_minutes = max(1, int(getattr(record, "timeout_minutes", 20) or 20))
    timed_out = _research_runtime_exceeds_timeout(record.created_at, timeout_minutes)
    if timed_out and not _is_terminal_research_status(status):
        remote_cancel_error = ""
        cancel_fn = getattr(refresh_client, "cancel_research", None)
        if callable(cancel_fn):
            try:
                cancel_fn(record.interaction_id)
            except Exception as e:
                remote_cancel_error = f"{type(e).__name__}: {e}"
        summary = f"Research job exceeded configured timeout ({timeout_minutes}m)"
        saved = save_research_job(
            ResearchJobRecord(
                interaction_id=record.interaction_id,
                status="error",
                prompt=record.prompt,
                agent=record.agent,
                created_at=record.created_at,
                updated_at=polled_at,
                summary=summary,
                output_text=output_text,
                error=(
                    f"Timed out after {timeout_minutes}m"
                    + (f"; remote cancel failed: {remote_cancel_error}" if remote_cancel_error else "")
                ),
                provider_status=status,
                last_polled_at=polled_at,
                poll_count=max(0, int(record.poll_count or 0)) + 1,
                timeout_minutes=timeout_minutes,
            )
        )
        _emit_job_completed_event(
            job_kind="research",
            job_id=f"research:{record.interaction_id}",
            status="error",
            summary=summary,
            hook_bus=hook_bus,
        )
        return _attach_refresh_meta(saved, attempted=True, ok=True, error="")
    summary = _summarize_research_state(status=status, output_text=output_text, fallback=record.summary)
    provider_status = status
    state_changed = (
        status != record.status
        or output_text != record.output_text
        or summary != record.summary
        or provider_status != (record.provider_status or "")
    )
    updated = ResearchJobRecord(
        interaction_id=record.interaction_id,
        status=status,
        prompt=record.prompt,
        agent=record.agent,
        created_at=record.created_at,
        updated_at=polled_at if state_changed else record.updated_at,
        summary=summary,
        output_text=output_text,
        error=record.error,
        provider_status=provider_status,
        last_polled_at=polled_at,
        poll_count=max(0, int(record.poll_count or 0)) + 1,
        timeout_minutes=timeout_minutes,
    )
    saved = save_research_job(updated)
    if state_changed:
        if _is_terminal_research_status(status):
            _emit_job_completed_event(
                job_kind="research",
                job_id=f"research:{record.interaction_id}",
                status=status,
                summary=summary,
                hook_bus=hook_bus,
            )
        else:
            _emit_job_progress_event(
                job_kind="research",
                job_id=f"research:{record.interaction_id}",
                status=status,
                summary=summary,
                hook_bus=hook_bus,
            )
    return _attach_refresh_meta(saved, attempted=True, ok=True, error="")


def _reconcile_local_research_job(record: ResearchJobRecord, *, hook_bus=None) -> ResearchJobRecord:
    if _is_terminal_research_status(record.status):
        return record
    if not _research_job_uses_stream(record):
        return record
    if _has_live_research_monitor(record.interaction_id):
        return record
    last_event_at = _parse_iso_datetime(record.last_event_at)
    if last_event_at is None:
        return record
    age_seconds = max(0, int((datetime.now(timezone.utc) - last_event_at).total_seconds()))
    if age_seconds < _STREAM_STALE_AFTER_SEC:
        return record
    saved = save_research_job(
        ResearchJobRecord(
            interaction_id=record.interaction_id,
            status="error",
            prompt=record.prompt,
            agent=record.agent,
            created_at=record.created_at,
            updated_at=_now_iso(),
            summary="Research stream inactive",
            output_text=record.output_text,
            error="No active stream consumer for this research job",
            provider_status=record.provider_status or record.status,
            last_polled_at=record.last_polled_at,
            last_event_at=record.last_event_at,
            stream_status=record.stream_status or "stream.inactive",
            latest_thought_summary=record.latest_thought_summary,
            poll_count=max(0, int(record.poll_count or 0)),
            timeout_minutes=max(1, int(record.timeout_minutes or 20)),
        )
    )
    _emit_job_completed_event(
        job_kind="research",
        job_id=f"research:{record.interaction_id}",
        status="error",
        summary=saved.summary,
        hook_bus=hook_bus,
    )
    return saved


def _attach_refresh_meta(record: ResearchJobRecord, *, attempted: bool, ok: bool, error: str) -> ResearchJobRecord:
    setattr(record, "_refresh_attempted", bool(attempted))
    setattr(record, "_refresh_ok", bool(ok))
    setattr(record, "_refresh_error", str(error or "").strip())
    return record


def _summarize_research_state(*, status: str, output_text: str, fallback: str) -> str:
    if output_text:
        first_line = output_text.splitlines()[0].strip()
        if first_line:
            return first_line
    normalized = str(status or "").strip().lower()
    if normalized in {"completed", "done"}:
        return "Research job completed"
    if normalized in {"failed", "error"}:
        return fallback or "Research job failed"
    if normalized == "cancelled":
        return fallback or "Research job cancelled"
    if normalized == "requires_action":
        return "Research job requires action"
    if normalized in {"queued", "starting"}:
        return "Research job queued"
    if normalized in {"running", "in_progress"}:
        if str(fallback or "").strip().lower() == "research job started":
            return "Research in progress"
        return fallback or "Research in progress"
    return fallback or normalized or "unknown"


def _is_terminal_research_status(status: str) -> bool:
    normalized = str(status or "").strip().lower()
    return normalized in {"completed", "done", "failed", "error", "cancelled"}


def _research_job_uses_stream(record: ResearchJobRecord) -> bool:
    return bool(str(getattr(record, "stream_status", "") or "").strip())


def _has_live_research_monitor(interaction_id: str) -> bool:
    normalized_id = str(interaction_id or "").strip()
    if not normalized_id:
        return False
    with _RESEARCH_MONITOR_LOCK:
        thread = _RESEARCH_MONITORS.get(normalized_id)
        return thread is not None and thread.is_alive()


def _research_runtime_exceeds_timeout(created_at: str, timeout_minutes: int) -> bool:
    started = _parse_iso_datetime(created_at)
    if started is None:
        return False
    elapsed_seconds = max(0, int((datetime.now(timezone.utc) - started).total_seconds()))
    return elapsed_seconds > max(1, int(timeout_minutes or 20)) * 60


def _parse_iso_datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _monitor_research_job_loop(
    interaction_id: str,
    refresh_client,
    poll_interval_sec: int,
    hook_bus,
) -> None:
    try:
        first_wait = min(max(1, int(poll_interval_sec or 10)), 2)
        wait_sec = first_wait
        while True:
            record = load_research_job(interaction_id)
            if record is None or _is_terminal_research_status(record.status):
                return
            time.sleep(wait_sec)
            refreshed = load_research_job(
                interaction_id,
                refresh_client=refresh_client,
                hook_bus=hook_bus,
            )
            if refreshed is None or _is_terminal_research_status(refreshed.status):
                return
            wait_sec = max(1, int(poll_interval_sec or 10))
    finally:
        with _RESEARCH_MONITOR_LOCK:
            current = _RESEARCH_MONITORS.get(interaction_id)
            if current is threading.current_thread():
                _RESEARCH_MONITORS.pop(interaction_id, None)


def _resolve_hook_bus(explicit_hook_bus=None, *, fallback_fn=None):
    if explicit_hook_bus is not None:
        return explicit_hook_bus
    if fallback_fn is not None:
        resolved = getattr(fallback_fn, "_hook_bus", None)
        if resolved is not None:
            return resolved
    return getattr(_emit_job_completed_event, "_hook_bus", None)


def _emit_job_progress_event(
    *,
    job_kind: str,
    job_id: str,
    status: str,
    summary: str,
    hook_bus=None,
) -> None:
    """Best-effort cross-surface notification when a research job makes progress."""
    try:
        from archon.ux.events import job_progress as _make_event
        from archon.control.hooks import HookBus
        from archon.control.contracts import HookEvent

        event = _make_event(job_kind=job_kind, job_id=job_id, status=status, summary=summary)
        resolved = _resolve_hook_bus(hook_bus, fallback_fn=_emit_job_progress_event)
        if isinstance(resolved, HookBus):
            resolved.emit(HookEvent(kind="ux.job_progress", payload={"event": event}))
    except Exception:
        pass


def _emit_job_completed_event(
    *,
    job_kind: str,
    job_id: str,
    status: str,
    summary: str,
    hook_bus=None,
) -> None:
    """Best-effort cross-surface notification when a research job completes."""
    try:
        from archon.ux.events import job_completed as _make_event
        from archon.control.hooks import HookBus
        from archon.control.contracts import HookEvent

        event = _make_event(job_kind=job_kind, job_id=job_id, status=status, summary=summary)
        resolved = _resolve_hook_bus(hook_bus, fallback_fn=_emit_job_completed_event)
        if isinstance(resolved, HookBus):
            resolved.emit(HookEvent(kind="ux.job_completed", payload={"event": event}))
    except Exception:
        pass
