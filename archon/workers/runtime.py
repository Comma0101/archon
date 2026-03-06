"""In-process background runtime for delegated worker runs (Phase 2 subprocess control)."""

import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from archon.execution.runner import run_worker_task
from archon.workers.base import WorkerEvent, WorkerExecObserver, WorkerResult, WorkerTask
from archon.workers.session_store import append_worker_events, record_worker_run, reserve_worker_session


@dataclass
class ActiveWorkerRun:
    session_id: str
    requested_worker: str
    state: str  # starting | running | completed | failed | cancelled
    started_at: str
    updated_at: str
    finished_at: str = ""
    error: str = ""
    cancel_requested: bool = False
    thread_name: str = ""
    pid: int = 0
    process_state: str = ""  # starting | running | exited
    process_returncode: int | None = None
    last_output_at: str = ""
    cancel_signal_sent: str = ""


_LOCK = threading.Lock()
_RUNS: dict[str, ActiveWorkerRun] = {}
_PROCESSES: dict[str, subprocess.Popen] = {}


def start_background_worker(task: WorkerTask, requested_worker: str) -> ActiveWorkerRun:
    """Reserve an Archon worker session and execute the delegated run in a background thread."""
    reserved = reserve_worker_session(task, requested_worker=requested_worker)
    task.archon_session_id = reserved.session_id

    now = _now_iso()
    run = ActiveWorkerRun(
        session_id=reserved.session_id,
        requested_worker=requested_worker or task.worker or "auto",
        state="starting",
        started_at=now,
        updated_at=now,
    )
    thread = threading.Thread(
        target=_background_run_main,
        args=(reserved.session_id, task, requested_worker),
        daemon=True,
        name=f"archon-worker-{reserved.session_id[:8]}",
    )
    run.thread_name = thread.name
    with _LOCK:
        _RUNS[reserved.session_id] = run
    thread.start()
    return run


def get_background_run(session_id: str) -> ActiveWorkerRun | None:
    with _LOCK:
        run = _RUNS.get(session_id)
        if run is None:
            return None
        return ActiveWorkerRun(**run.__dict__)


def list_background_runs(active_only: bool = True) -> list[ActiveWorkerRun]:
    with _LOCK:
        runs = [ActiveWorkerRun(**r.__dict__) for r in _RUNS.values()]
    if active_only:
        runs = [r for r in runs if r.state in {"starting", "running"}]
    runs.sort(key=lambda r: (r.updated_at, r.session_id), reverse=True)
    return runs


def request_background_cancel(session_id: str) -> bool:
    proc: subprocess.Popen | None = None
    with _LOCK:
        run = _RUNS.get(session_id)
        if run is None:
            return False
        run.cancel_requested = True
        if run.state in {"starting", "running"}:
            run.state = "cancelled"
        run.updated_at = _now_iso()
        proc = _PROCESSES.get(session_id)
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            _update_run(session_id, cancel_signal_sent="terminate")
        except Exception:
            pass

        def _kill_later():
            try:
                proc.wait(timeout=1.5)
                return
            except Exception:
                pass
            if proc.poll() is None:
                try:
                    proc.kill()
                    _update_run(session_id, cancel_signal_sent="kill")
                except Exception:
                    pass

        threading.Thread(target=_kill_later, daemon=True, name=f"archon-worker-kill-{session_id[:8]}").start()
    return True


def _background_run_main(session_id: str, task: WorkerTask, requested_worker: str):
    _update_run(session_id, state="running")
    observer = _RuntimeExecObserver(session_id)
    try:
        result = run_worker_task(task, exec_observer=observer)
    except Exception as e:
        result = WorkerResult(
            worker=task.worker or requested_worker or "auto",
            status="error",
            summary=f"Background worker runtime error: {type(e).__name__}",
            repo_path=task.repo_path,
            error=str(e),
        )
    try:
        record_worker_run(task, result, requested_worker=requested_worker)
    except Exception as e:
        _update_run(session_id, state="failed", error=f"record_worker_run failed: {type(e).__name__}: {e}")
        _clear_process(session_id)
        return

    _clear_process(session_id)
    run = get_background_run(session_id)
    if run and run.cancel_requested:
        _update_run(session_id, state="cancelled")
    else:
        terminal_state = "completed" if result.status in {"ok", "failed", "timeout", "cancelled", "unsupported", "unavailable", "error"} else "completed"
        _update_run(session_id, state=terminal_state)


def _update_run(
    session_id: str,
    state: str | None = None,
    error: str | None = None,
    pid: int | None = None,
    process_state: str | None = None,
    process_returncode: int | None = None,
    last_output_at: str | None = None,
    cancel_signal_sent: str | None = None,
):
    with _LOCK:
        run = _RUNS.get(session_id)
        if run is None:
            return
        if state is not None:
            run.state = state
        if error is not None:
            run.error = error
        if pid is not None:
            run.pid = pid
        if process_state is not None:
            run.process_state = process_state
        if process_returncode is not None:
            run.process_returncode = process_returncode
        if last_output_at is not None:
            run.last_output_at = last_output_at
        if cancel_signal_sent is not None:
            run.cancel_signal_sent = cancel_signal_sent
        run.updated_at = _now_iso()
        if run.state in {"completed", "failed", "cancelled"} and not run.finished_at:
            run.finished_at = run.updated_at


def _set_process(session_id: str, proc: subprocess.Popen):
    with _LOCK:
        _PROCESSES[session_id] = proc


def _clear_process(session_id: str):
    with _LOCK:
        _PROCESSES.pop(session_id, None)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class _RuntimeExecObserver(WorkerExecObserver):
    """Bridges adapter subprocess lifecycle callbacks into runtime state + session event log."""

    def __init__(self, session_id: str):
        self.session_id = session_id

    def on_process_started(self, process):
        _set_process(self.session_id, process)
        pid = int(getattr(process, "pid", 0) or 0)
        _update_run(self.session_id, pid=pid, process_state="running")
        append_worker_events(
            self.session_id,
            [WorkerEvent(kind="runtime.process.started", payload={"pid": pid})],
        )

    def on_process_output(self, stream: str, text: str):
        line = _normalize_output_text(text)
        if not line:
            return
        now = _now_iso()
        _update_run(self.session_id, last_output_at=now)
        append_worker_events(
            self.session_id,
            [
                WorkerEvent(
                    kind=f"runtime.{stream}.line",
                    payload={"text": line, "timestamp": now},
                )
            ],
        )

    def on_process_exit(self, returncode: int | None):
        _update_run(self.session_id, process_state="exited", process_returncode=returncode)
        append_worker_events(
            self.session_id,
            [WorkerEvent(kind="runtime.process.exited", payload={"returncode": returncode})],
        )

    def on_process_signal(self, signal_name: str, reason: str = ""):
        _update_run(self.session_id, cancel_signal_sent=signal_name)
        append_worker_events(
            self.session_id,
            [
                WorkerEvent(
                    kind="runtime.process.signal",
                    payload={"signal": signal_name, "reason": reason},
                )
            ],
        )

    def is_cancel_requested(self) -> bool:
        with _LOCK:
            run = _RUNS.get(self.session_id)
            return bool(run.cancel_requested) if run is not None else False


def _normalize_output_text(text: str, max_chars: int = 2000) -> str:
    value = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    value = value.rstrip("\n")
    if not value:
        return ""
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + f"... (truncated {len(value) - max_chars} chars)"
