"""Worker tool registrations facade and delegate/session helper routines."""

import sys

from archon.control import session_controller
from archon.execution.runner import run_worker_task
from archon.workers import (
    WorkerTask,
    append_worker_turn,
    cancel_worker_session,
    decide_worker_approval,
    format_worker_approvals,
    format_worker_result,
    format_worker_session_list,
    format_worker_session_record,
    get_background_run,
    list_worker_approvals,
    list_worker_sessions,
    load_worker_events,
    load_worker_result,
    load_worker_session,
    load_worker_task,
    record_worker_run,
    reconcile_worker_session,
    reserve_worker_session,
    request_background_cancel,
    start_background_worker,
)

from .common import truncate_text as _truncate
from .worker_delegate_tools import register_delegate_tool
from .worker_session_tools import register_worker_session_tools


def register_worker_tools(registry) -> None:
    """Register worker/delegation tools (delegated to extracted modules)."""
    ns = sys.modules[__name__]
    session_handlers = register_worker_session_tools(registry, ns)
    register_delegate_tool(registry, ns, session_handlers["worker_send"])


def _runtime_quiet_seconds(active_run) -> int | None:
    return session_controller.runtime_quiet_seconds(active_run)


def _choose_delegate_execution_mode(
    *,
    task: str,
    mode: str,
    timeout_sec: int,
    requested_execution_mode: str,
) -> tuple[str, str]:
    return session_controller.choose_delegate_execution_mode(
        task=task,
        mode=mode,
        timeout_sec=timeout_sec,
        requested_execution_mode=requested_execution_mode,
    )


def _detect_delegate_continue_target_worker(
    *,
    task: str,
    requested_worker: str,
    requested_execution_mode: str,
) -> str:
    return session_controller.detect_delegate_continue_target_worker(
        task=task,
        requested_worker=requested_worker,
        requested_execution_mode=requested_execution_mode,
    )


def _detect_delegate_force_new_session(task: str) -> bool:
    return session_controller.detect_delegate_force_new_session(task)


def _worker_supporting_resume_key(worker: str) -> str:
    return session_controller.worker_supporting_resume_key(worker)


def _find_latest_worker_session_for_repo(*, worker: str, repo_path: str):
    return session_controller.find_latest_worker_session_for_repo(
        worker=worker,
        repo_path=repo_path,
        list_sessions_fn=list_worker_sessions,
    )
