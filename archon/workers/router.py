"""Worker router that normalizes delegated coding tasks to adapters."""

import shutil

from archon.workers.base import WorkerExecObserver, WorkerResult, WorkerTask
from archon.workers.claude_code_cli import run_claude_code_task
from archon.workers.codex_cli import run_codex_task
from archon.workers.opencode_cli import run_opencode_task


def run_worker_task(task: WorkerTask, exec_observer: WorkerExecObserver | None = None) -> WorkerResult:
    worker = _normalize_worker(task.worker)
    if worker == "auto":
        worker = _pick_auto_worker()

    normalized_task = WorkerTask(
        task=task.task,
        worker=worker,
        mode=(task.mode or "implement"),
        repo_path=task.repo_path,
        timeout_sec=task.timeout_sec,
        constraints=task.constraints,
        model=task.model,
        resume_vendor_session_id=task.resume_vendor_session_id,
        archon_session_id=task.archon_session_id,
    )

    if worker == "codex":
        return run_codex_task(normalized_task, exec_observer=exec_observer)

    if worker == "claude_code":
        return run_claude_code_task(normalized_task, exec_observer=exec_observer)
    if worker == "opencode":
        return run_opencode_task(normalized_task, exec_observer=exec_observer)

    return WorkerResult(
        worker=worker,
        status="unsupported",
        summary=f"Unknown worker '{task.worker}'",
        repo_path=normalized_task.repo_path,
        error="Supported workers: auto, codex, claude_code, opencode",
    )


def _normalize_worker(worker: str) -> str:
    value = (worker or "auto").strip().lower().replace("-", "_")
    aliases = {
        "claude": "claude_code",
        "claudecode": "claude_code",
    }
    return aliases.get(value, value)


def _pick_auto_worker() -> str:
    if shutil.which("codex"):
        return "codex"
    if shutil.which("claude"):
        return "claude_code"
    if shutil.which("opencode"):
        return "opencode"
    return "codex"


def _unsupported(worker: str, message: str, task: WorkerTask) -> WorkerResult:
    return WorkerResult(
        worker=worker,
        status="unsupported",
        summary=message,
        repo_path=task.repo_path,
        error=message,
    )
