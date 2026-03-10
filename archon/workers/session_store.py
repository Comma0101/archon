"""Persistent storage for delegated worker sessions and event logs."""

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from archon.control.jobs import JobSummary, summarize_worker_session
from archon.config import STATE_DIR
from archon.workers.base import WorkerEvent, WorkerResult, WorkerTask
from archon.workers.session_store_format import (
    format_worker_approvals,
    format_worker_session_list,
    format_worker_session_record,
)
from archon.workers.session_store_memory_capture import (
    maybe_queue_worker_summary_candidate as _queue_worker_summary_candidate_impl,
    resolve_worker_summary_target as _resolve_worker_summary_target_impl,
)
from archon.workers.session_store_models import WorkerApprovalRequest, WorkerSessionRecord


WORKERS_STATE_DIR = STATE_DIR / "workers"
WORKER_SESSIONS_DIR = WORKERS_STATE_DIR / "sessions"
WORKER_EVENTS_DIR = WORKERS_STATE_DIR / "events"
_STORE_LOCK = threading.RLock()


def record_worker_run(
    task: WorkerTask,
    result: WorkerResult,
    requested_worker: str,
    *,
    hook_bus=None,
) -> WorkerSessionRecord:
    """Persist a completed delegated worker run and return its Archon session record."""
    with _STORE_LOCK:
        _ensure_dirs()
        if task.archon_session_id:
            payload, record = _load_payload_and_record(task.archon_session_id)
        else:
            payload, record = None, None

        if payload is None or record is None:
            record = reserve_worker_session(task, requested_worker=requested_worker)
            payload, record = _load_payload_and_record(record.session_id)
            if payload is None or record is None:
                raise RuntimeError("Failed to initialize worker session payload")

        now = _now_iso()
        preserve_cancelled = bool(record.cancelled_at) or record.status == "cancelled"
        record.updated_at = now
        record.completed_at = now
        record.requested_worker = (requested_worker or task.worker or record.requested_worker or "auto")
        record.selected_worker = result.worker or record.selected_worker
        record.mode = task.mode or record.mode
        if not preserve_cancelled:
            record.status = result.status
        record.repo_path = str(Path(task.repo_path).expanduser().resolve())
        record.task = task.task
        record.constraints = task.constraints
        record.timeout_sec = int(task.timeout_sec)
        record.summary = result.summary
        record.exit_code = result.exit_code
        if not preserve_cancelled or not record.error:
            record.error = result.error
        if result.vendor_session_id:
            record.vendor_session_id = result.vendor_session_id

        turns = payload.get("turns")
        if not isinstance(turns, list):
            turns = _infer_turns_from_legacy_payload(payload)

        append_events = (WORKER_EVENTS_DIR / f"{record.session_id}.jsonl").exists()
        if record.turn_count <= 0:
            record.turn_count = 1
            turns.append(_turn_entry(1, task, result))
        else:
            if turns:
                # If a provisional record already exists with no turns, normalize.
                record.turn_count = max(1, int(record.turn_count))
                if len(turns) < record.turn_count:
                    turns.append(_turn_entry(record.turn_count, task, result))
                else:
                    turns[-1] = _turn_entry(record.turn_count, task, result)
            else:
                record.turn_count = 1
                turns.append(_turn_entry(1, task, result))

        record.event_count = int(record.event_count) + len(result.events)
        payload["record"] = record.to_dict()
        payload["task"] = task.to_dict()
        payload["result"] = result.to_dict(include_output=True)
        payload["turns"] = turns
        payload.setdefault("approval_requests", [])
        _write_payload(record.session_id, payload)
        _append_event_log(record.session_id, result.events, append=append_events)
        _maybe_queue_worker_summary_candidate(record, task, result)
        _emit_job_completed_event(
            job_kind="worker",
            job_id=record.session_id,
            status=record.status,
            summary=record.summary,
            hook_bus=hook_bus,
        )
        return record


def reserve_worker_session(task: WorkerTask, requested_worker: str) -> WorkerSessionRecord:
    """Create a provisional worker session before launching a delegated worker."""
    with _STORE_LOCK:
        _ensure_dirs()
        now = _now_iso()
        session_id = task.archon_session_id.strip() if task.archon_session_id else str(uuid.uuid4())
        path = WORKER_SESSIONS_DIR / f"{session_id}.json"
        if path.exists():
            existing = load_worker_session(session_id)
            if existing is not None:
                return existing

        record = WorkerSessionRecord(
            session_id=session_id,
            created_at=now,
            updated_at=now,
            completed_at="",
            requested_worker=(requested_worker or task.worker or "auto"),
            selected_worker="",
            mode=task.mode,
            status="running",
            repo_path=str(Path(task.repo_path).expanduser().resolve()),
            task=task.task,
            constraints=task.constraints,
            timeout_sec=int(task.timeout_sec),
            summary="Worker session reserved",
            exit_code=None,
            error="",
            vendor_session_id="",
            event_count=0,
            turn_count=0,
        )
        _write_session_record(
            record,
            task,
            WorkerResult(worker="", status="running", repo_path=record.repo_path),
            turns=[],
        )
        return record


def append_worker_turn(session_id: str, task: WorkerTask, result: WorkerResult) -> WorkerSessionRecord | None:
    with _STORE_LOCK:
        payload, record = _load_payload_and_record(session_id)
        if payload is None or record is None:
            return None

        now = _now_iso()
        record.updated_at = now
        record.completed_at = now
        preserve_cancelled = bool(record.cancelled_at) or record.status == "cancelled"
        if not preserve_cancelled:
            record.status = result.status
        record.summary = result.summary
        record.exit_code = result.exit_code
        if not preserve_cancelled or not record.error:
            record.error = result.error
        if result.vendor_session_id:
            record.vendor_session_id = result.vendor_session_id
        record.selected_worker = result.worker or record.selected_worker
        record.mode = task.mode or record.mode
        record.event_count = int(record.event_count) + len(result.events)
        record.turn_count = max(1, int(record.turn_count)) + 1

        turns = payload.get("turns")
        if not isinstance(turns, list):
            turns = _infer_turns_from_legacy_payload(payload)
        turns.append(_turn_entry(record.turn_count, task, result))

        payload["record"] = record.to_dict()
        payload["task"] = task.to_dict()
        payload["result"] = result.to_dict(include_output=True)
        payload["turns"] = turns

        _write_payload(session_id, payload)
        _append_event_log(session_id, result.events, append=True)
        _maybe_queue_worker_summary_candidate(record, task, result)
        return record


def append_worker_events(session_id: str, events: list[WorkerEvent]) -> WorkerSessionRecord | None:
    """Append normalized events to an existing worker session while it is still running."""
    with _STORE_LOCK:
        if not events:
            return load_worker_session(session_id)
        payload, record = _load_payload_and_record(session_id)
        if payload is None or record is None:
            return None
        record.updated_at = _now_iso()
        record.event_count = int(record.event_count) + len(events)
        payload["record"] = record.to_dict()
        _write_payload(session_id, payload)
        _append_event_log(session_id, events, append=True)
        return record


def add_worker_approval_request(
    session_id: str,
    action: str,
    details: str,
) -> WorkerApprovalRequest | None:
    with _STORE_LOCK:
        payload, record = _load_payload_and_record(session_id)
        if payload is None or record is None:
            return None
        approvals = _load_approvals_from_payload(payload)
        req = WorkerApprovalRequest(
            request_id=str(uuid.uuid4()),
            status="pending",
            action=action,
            details=details,
            created_at=_now_iso(),
        )
        approvals.append(req)
        payload["approval_requests"] = [a.to_dict() for a in approvals]
        record.updated_at = _now_iso()
        if record.status != "cancelled":
            record.status = "waiting_approval"
        payload["record"] = record.to_dict()
        _write_payload(session_id, payload)
        _append_event_log(
            session_id,
            [WorkerEvent(kind="approval.requested", payload=req.to_dict())],
            append=True,
        )
        record.event_count += 1
        payload["record"] = record.to_dict()
        _write_payload(session_id, payload)
        return req


def list_worker_approvals(session_id: str, pending_only: bool = False) -> list[WorkerApprovalRequest]:
    with _STORE_LOCK:
        payload, _record = _load_payload_and_record(session_id)
        if payload is None:
            return []
        approvals = _load_approvals_from_payload(payload)
        if pending_only:
            approvals = [a for a in approvals if a.status == "pending"]
        approvals.sort(key=lambda a: (a.created_at, a.request_id))
        return approvals


def decide_worker_approval(
    session_id: str,
    request_id: str,
    decision: str,
    note: str = "",
) -> WorkerApprovalRequest | None:
    decision_value = decision.strip().lower()
    if decision_value not in {"approve", "approved", "deny", "denied"}:
        return None
    with _STORE_LOCK:
        payload, record = _load_payload_and_record(session_id)
        if payload is None or record is None:
            return None
        approvals = _load_approvals_from_payload(payload)
        target: WorkerApprovalRequest | None = None
        for approval in approvals:
            if approval.request_id == request_id:
                target = approval
                break
        if target is None:
            return None
        target.status = "approved" if decision_value.startswith("approv") else "denied"
        target.decision = target.status
        target.decided_at = _now_iso()
        target.note = note

        payload["approval_requests"] = [a.to_dict() for a in approvals]
        record.updated_at = _now_iso()
        if record.status == "waiting_approval" and not any(a.status == "pending" for a in approvals):
            record.status = "paused"
        payload["record"] = record.to_dict()
        _write_payload(session_id, payload)
        _append_event_log(
            session_id,
            [WorkerEvent(kind="approval.decided", payload=target.to_dict())],
            append=True,
        )
        record.event_count += 1
        payload["record"] = record.to_dict()
        _write_payload(session_id, payload)
        return target


def cancel_worker_session(session_id: str, reason: str = "Cancelled by user") -> WorkerSessionRecord | None:
    with _STORE_LOCK:
        payload, record = _load_payload_and_record(session_id)
        if payload is None or record is None:
            return None
        now = _now_iso()
        record.updated_at = now
        record.completed_at = now
        record.cancelled_at = now
        record.status = "cancelled"
        if reason and not record.error:
            record.error = reason
        payload["record"] = record.to_dict()
        _write_payload(session_id, payload)
        _append_event_log(
            session_id,
            [WorkerEvent(kind="session.cancelled", payload={"reason": reason, "timestamp": now})],
            append=True,
        )
        record.event_count += 1
        payload["record"] = record.to_dict()
        _write_payload(session_id, payload)
        return record


def reconcile_worker_session(
    session_id: str,
    *,
    reason: str = "Reconciled orphaned worker session",
    terminal_status: str = "error",
) -> WorkerSessionRecord | None:
    """Force-finalize a stuck worker session record when no live runtime can finish it."""
    if terminal_status not in {"error", "failed", "cancelled", "paused"}:
        terminal_status = "error"
    with _STORE_LOCK:
        payload, record = _load_payload_and_record(session_id)
        if payload is None or record is None:
            return None
        if record.status in {"ok", "failed", "timeout", "unsupported", "unavailable", "error", "cancelled"} and record.completed_at:
            return record

        now = _now_iso()
        record.updated_at = now
        if not record.completed_at:
            record.completed_at = now
        if record.status != "cancelled":
            record.status = terminal_status
        if reason:
            if not record.summary or record.summary == "Worker session reserved":
                record.summary = reason
            if not record.error and terminal_status in {"error", "failed"}:
                record.error = reason
        payload["record"] = record.to_dict()
        _write_payload(session_id, payload)
        _append_event_log(
            session_id,
            [WorkerEvent(kind="session.reconciled", payload={"reason": reason, "status": record.status, "timestamp": now})],
            append=True,
        )
        record.event_count += 1
        payload["record"] = record.to_dict()
        _write_payload(session_id, payload)
        return record


def _maybe_reconcile_stale_reserved_session(record: WorkerSessionRecord | None) -> WorkerSessionRecord | None:
    if record is None:
        return None
    if str(record.status or "").strip().lower() not in {"running", "starting"}:
        return record
    if str(record.summary or "").strip() != "Worker session reserved":
        return record
    try:
        from archon.workers.runtime import get_background_run
    except Exception:
        get_background_run = None
    active_run = None
    if callable(get_background_run):
        try:
            active_run = get_background_run(record.session_id)
        except Exception:
            active_run = None
    if active_run is not None and str(getattr(active_run, "state", "") or "").strip().lower() in {
        "starting",
        "running",
    }:
        return record
    reconciled = reconcile_worker_session(
        record.session_id,
        reason="Worker session never started",
        terminal_status="error",
    )
    return reconciled or record


def load_worker_session(session_id: str) -> WorkerSessionRecord | None:
    with _STORE_LOCK:
        path = WORKER_SESSIONS_DIR / f"{session_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
        except Exception:
            return None
        record_data = data.get("record", data)
        if not isinstance(record_data, dict):
            return None
        return WorkerSessionRecord.from_dict(record_data)


def load_worker_result(session_id: str) -> WorkerResult | None:
    with _STORE_LOCK:
        path = WORKER_SESSIONS_DIR / f"{session_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
        except Exception:
            return None
        result_data = data.get("result")
        if not isinstance(result_data, dict):
            return None
        return WorkerResult.from_dict(result_data)


def load_worker_job_summary(session_id: str) -> JobSummary | None:
    record = _maybe_reconcile_stale_reserved_session(load_worker_session(session_id))
    if record is None:
        return None
    return summarize_worker_session(record)


def load_worker_task(session_id: str) -> WorkerTask | None:
    with _STORE_LOCK:
        path = WORKER_SESSIONS_DIR / f"{session_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
        except Exception:
            return None
        task_data = data.get("task")
        if not isinstance(task_data, dict):
            return None
        try:
            return WorkerTask(
                task=str(task_data.get("task", "")),
                worker=str(task_data.get("worker", "")),
                mode=str(task_data.get("mode", "implement")),
                repo_path=str(task_data.get("repo_path", ".")),
                timeout_sec=int(task_data.get("timeout_sec", 900)),
                constraints=str(task_data.get("constraints", "")),
                model=str(task_data.get("model", "")),
                resume_vendor_session_id=str(task_data.get("resume_vendor_session_id", "")),
                archon_session_id=str(task_data.get("archon_session_id", "")),
            )
        except Exception:
            return None


def load_worker_events(session_id: str, limit: int = 200) -> list[WorkerEvent]:
    with _STORE_LOCK:
        path = WORKER_EVENTS_DIR / f"{session_id}.jsonl"
        if not path.exists():
            return []
        events: list[WorkerEvent] = []
        try:
            lines = path.read_text().splitlines()
        except Exception:
            return []
        selected = lines[-max(1, int(limit)):] if limit else lines
        for line in selected:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                events.append(WorkerEvent.from_dict(data))
        return events


def list_worker_sessions(limit: int = 20) -> list[WorkerSessionRecord]:
    with _STORE_LOCK:
        if not WORKER_SESSIONS_DIR.exists():
            return []
        records: list[WorkerSessionRecord] = []
        for path in sorted(WORKER_SESSIONS_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text())
            except Exception:
                continue
            record_data = data.get("record", data)
            if isinstance(record_data, dict):
                try:
                    records.append(WorkerSessionRecord.from_dict(record_data))
                except Exception:
                    continue
        records.sort(key=lambda r: (r.updated_at, r.session_id), reverse=True)
        return records[: max(1, int(limit))]


def list_worker_job_summaries(limit: int = 20) -> list[JobSummary]:
    records = [_maybe_reconcile_stale_reserved_session(record) for record in list_worker_sessions(limit=limit)]
    normalized = [summarize_worker_session(record) for record in records if record is not None]
    normalized.sort(key=lambda job: (job.last_update_at, job.job_id), reverse=True)
    return normalized


def purge_stale_sessions(statuses: list[str] | None = None) -> int:
    """Remove worker sessions with given statuses. Returns count removed."""
    if statuses is None:
        statuses = ["error", "cancelled"]
    removed = 0
    with _STORE_LOCK:
        if not WORKER_SESSIONS_DIR.exists():
            return 0
        for path in list(WORKER_SESSIONS_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text())
            except Exception:
                continue
            record_data = data.get("record", data)
            if not isinstance(record_data, dict):
                continue
            status = str(record_data.get("status", "")).strip().lower()
            if status in statuses:
                session_id = path.stem
                path.unlink(missing_ok=True)
                # Also remove associated event log
                event_path = WORKER_EVENTS_DIR / f"{session_id}.jsonl"
                if event_path.exists():
                    event_path.unlink(missing_ok=True)
                removed += 1
    return removed


def _write_session_record(
    record: WorkerSessionRecord,
    task: WorkerTask,
    result: WorkerResult,
    turns: list[dict] | None = None,
):
    payload = {
        "record": record.to_dict(),
        "task": task.to_dict(),
        "result": result.to_dict(include_output=True),
        "turns": turns or [],
        "approval_requests": [],
    }
    _write_payload(record.session_id, payload)


def _append_event_log(session_id: str, events: list[WorkerEvent], append: bool):
    path = WORKER_EVENTS_DIR / f"{session_id}.jsonl"
    mode = "a" if append else "w"
    with path.open(mode) as f:
        for event in events:
            f.write(json.dumps(event.to_dict(), sort_keys=True))
            f.write("\n")


def _write_payload(session_id: str, payload: dict):
    path = WORKER_SESSIONS_DIR / f"{session_id}.json"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    tmp.replace(path)


def _load_payload_and_record(session_id: str) -> tuple[dict | None, WorkerSessionRecord | None]:
    path = WORKER_SESSIONS_DIR / f"{session_id}.json"
    if not path.exists():
        return None, None
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return None, None
    record_data = payload.get("record", payload)
    if not isinstance(record_data, dict):
        return None, None
    try:
        record = WorkerSessionRecord.from_dict(record_data)
    except Exception:
        return None, None
    return payload, record


def _infer_turns_from_legacy_payload(payload: dict) -> list[dict]:
    task_data = payload.get("task")
    result_data = payload.get("result")
    if isinstance(task_data, dict) and isinstance(result_data, dict):
        return [{
            "turn": 1,
            "timestamp": _now_iso(),
            "task": task_data,
            "result": result_data,
        }]
    return []


def _maybe_queue_worker_summary_candidate(
    record: WorkerSessionRecord,
    task: WorkerTask,
    result: WorkerResult,
) -> None:
    """Wrapper kept for monkeypatch/test seams."""
    _queue_worker_summary_candidate_impl(record, task, result)


def _resolve_worker_summary_target(repo_path: str, repo_name: str) -> tuple[str, str] | None:
    """Wrapper kept for monkeypatch/test seams."""
    return _resolve_worker_summary_target_impl(repo_path, repo_name)


def _load_approvals_from_payload(payload: dict) -> list[WorkerApprovalRequest]:
    raw = payload.get("approval_requests", [])
    if not isinstance(raw, list):
        return []
    approvals: list[WorkerApprovalRequest] = []
    for item in raw:
        if isinstance(item, dict):
            try:
                approvals.append(WorkerApprovalRequest.from_dict(item))
            except Exception:
                continue
    return approvals


def _turn_entry(turn: int, task: WorkerTask, result: WorkerResult) -> dict:
    return {
        "turn": int(turn),
        "timestamp": _now_iso(),
        "task": task.to_dict(),
        "result": result.to_dict(include_output=True),
    }


def _ensure_dirs():
    WORKER_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    WORKER_EVENTS_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _emit_job_completed_event(
    *,
    job_kind: str,
    job_id: str,
    status: str,
    summary: str,
    hook_bus=None,
) -> None:
    """Best-effort cross-surface notification when a worker job completes."""
    try:
        from archon.ux.events import job_completed as _make_event
        from archon.control.contracts import HookEvent

        event = _make_event(job_kind=job_kind, job_id=job_id, status=status, summary=summary)
        if hook_bus is not None and hasattr(hook_bus, "emit"):
            hook_bus.emit(HookEvent(kind="ux.job_completed", payload={"event": event}))
    except Exception:
        pass
