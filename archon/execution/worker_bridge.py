"""Execution bridge that delegates to the existing worker router implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from archon.workers.base import WorkerExecObserver, WorkerResult, WorkerTask
else:
    WorkerExecObserver = Any
    WorkerResult = Any
    WorkerTask = Any


def run_worker_task_legacy(
    task: WorkerTask,
    exec_observer: WorkerExecObserver | None = None,
) -> WorkerResult:
    """Run delegated worker tasks through the legacy router path."""
    from archon.workers.router import run_worker_task as _legacy_run_worker_task

    return _legacy_run_worker_task(task, exec_observer=exec_observer)
