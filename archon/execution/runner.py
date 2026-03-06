"""Execution-plane task runner with backend dispatch."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from archon.execution.worker_bridge import run_worker_task_legacy

if TYPE_CHECKING:
    from archon.workers.base import WorkerExecObserver, WorkerResult, WorkerTask
else:
    WorkerExecObserver = Any
    WorkerResult = Any
    WorkerTask = Any


_SUPPORTED_BACKENDS = {"host", "subprocess-restricted", "container"}


def run_task(
    task: WorkerTask,
    *,
    execution_backend: str = "host",
    exec_observer: WorkerExecObserver | None = None,
) -> WorkerResult:
    """Run a worker task via the configured backend.

    Phase 2 keeps all supported backends routed through the legacy router while the
    execution plane boundary is introduced.
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

    return run_worker_task_legacy(task, exec_observer=exec_observer)


def run_worker_task(
    task: WorkerTask,
    exec_observer: WorkerExecObserver | None = None,
) -> WorkerResult:
    """Compatibility alias used by existing worker tooling imports."""
    return run_task(task, execution_backend="host", exec_observer=exec_observer)
