"""Execution-plane primitives for orchestrator migration."""

from .contracts import ExecutionBackendInfo, ExecutionRuntimeResult
from .runner import run_task, run_worker_task

__all__ = [
    "ExecutionBackendInfo",
    "ExecutionRuntimeResult",
    "run_task",
    "run_worker_task",
]
