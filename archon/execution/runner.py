"""Execution-plane task runner with backend dispatch."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from archon.workers.base import WorkerExecObserver, WorkerResult, WorkerTask
else:
    WorkerExecObserver = Any
    WorkerResult = Any
    WorkerTask = Any


_SUPPORTED_BACKENDS = {"host", "subprocess-restricted", "container"}
_NOT_IMPLEMENTED_BACKENDS = {"subprocess-restricted", "container"}


def _run_host_worker_task(
    task: WorkerTask,
    *,
    exec_observer: WorkerExecObserver | None = None,
) -> WorkerResult:
    from archon.workers.router import run_worker_task as _run_worker_task_router

    return _run_worker_task_router(task, exec_observer=exec_observer)


def run_task(
    task: WorkerTask,
    *,
    execution_backend: str = "host",
    exec_observer: WorkerExecObserver | None = None,
) -> WorkerResult:
    """Run a worker task via the configured backend.

    The host backend dispatches directly through the worker router. Other declared
    backends are kept as truthful placeholders until real isolation backends land.
    """
    backend = (execution_backend or "host").strip().lower()
    if backend not in _SUPPORTED_BACKENDS:
        from archon.workers.base import WorkerResult as _WorkerResult

        return _WorkerResult(
            worker=(task.worker or "").strip().lower() or "auto",
            status="unsupported",
            summary=f"Unsupported execution backend '{execution_backend}'",
            repo_path=task.repo_path,
            error=f"Supported execution backends: {', '.join(sorted(_SUPPORTED_BACKENDS))}",
        )

    if backend in _NOT_IMPLEMENTED_BACKENDS:
        from archon.workers.base import WorkerResult as _WorkerResult

        return _WorkerResult(
            worker=(task.worker or "").strip().lower() or "auto",
            status="unsupported",
            summary=f"Execution backend '{backend}' is not implemented yet",
            repo_path=task.repo_path,
            error="Only 'host' is currently supported for worker execution.",
        )

    return _run_host_worker_task(task, exec_observer=exec_observer)


def run_worker_task(
    task: WorkerTask,
    exec_observer: WorkerExecObserver | None = None,
) -> WorkerResult:
    """Compatibility alias used by existing worker tooling imports."""
    return run_task(task, execution_backend="host", exec_observer=exec_observer)
