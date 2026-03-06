"""Session and delegation heuristics for control-plane orchestration."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def runtime_quiet_seconds(active_run: Any) -> int | None:
    timestamp = ""
    if getattr(active_run, "last_output_at", ""):
        timestamp = active_run.last_output_at
    elif getattr(active_run, "updated_at", ""):
        timestamp = active_run.updated_at
    if not timestamp:
        return None
    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    seconds = int((now - dt).total_seconds())
    return max(0, seconds)


def choose_delegate_execution_mode(
    *,
    task: str,
    mode: str,
    timeout_sec: int,
    requested_execution_mode: str,
) -> tuple[str, str]:
    requested = (requested_execution_mode or "auto").strip().lower()
    if requested in {"one-shot", "one_shot", "single", "single_shot"}:
        requested = "oneshot"
    if requested in {"session", "bg"}:
        requested = "background"
    if requested not in {"auto", "oneshot", "background"}:
        return "invalid", (
            f"invalid execution_mode '{requested_execution_mode}' "
            "(expected auto|oneshot|background)"
        )
    if requested != "auto":
        return requested, "explicit_request"

    mode_value = (mode or "").strip().lower()
    if mode_value in {"implement", "debug"}:
        return "background", "mode_requires_session"

    task_l = (task or "").lower()
    scope_keywords = (
        "repo",
        "repository",
        "project",
        "codebase",
        "folder",
        "directory",
    )
    broad_keywords = (
        "deep",
        "comprehensive",
        "thorough",
        "entire",
        "whole",
        "full",
        "end-to-end",
        "end to end",
    )
    understand_keywords = (
        "understand",
        "map out",
        "learn",
        "review architecture",
        "architecture review",
    )
    has_scope = any(k in task_l for k in scope_keywords)
    has_broad = any(k in task_l for k in broad_keywords)
    has_understand = any(k in task_l for k in understand_keywords)
    if has_scope and (has_broad or has_understand):
        return "background", "deep_scope_request"

    if int(timeout_sec) >= 1200 and mode_value in {"review", "analyze"}:
        return "background", "long_timeout_review"

    return "oneshot", "auto_small_task"


def detect_delegate_continue_target_worker(
    *,
    task: str,
    requested_worker: str,
    requested_execution_mode: str,
) -> str:
    exec_mode = (requested_execution_mode or "auto").strip().lower()
    if exec_mode in {"oneshot", "one-shot", "one_shot", "single", "single_shot"}:
        return ""

    task_l = (task or "").lower()
    has_continue = any(
        token in task_l
        for token in (
            "continue",
            "resume",
            "follow up",
            "follow-up",
            "keep going",
            "keep working",
            "continue with",
        )
    )
    has_session_ref = any(
        token in task_l
        for token in (
            "same session",
            "that session",
            "previous session",
            "existing session",
            "worker session",
            "session",
        )
    )
    if not (has_continue and has_session_ref):
        return ""

    worker_value = (requested_worker or "").strip().lower()
    if worker_value in {"claude_code", "opencode"}:
        return worker_value
    if "opencode" in task_l:
        return "opencode"
    if "claude code" in task_l or "claude" in task_l:
        return "claude_code"
    if "codex" in task_l:
        return "codex"
    return ""


def detect_delegate_force_new_session(task: str) -> bool:
    task_l = (task or "").lower()
    explicit_new_patterns = (
        "start a new session",
        "start new session",
        "new session",
        "fresh session",
        "new opencode session",
        "new claude session",
        "new claude code session",
        "new codex session",
        "fresh opencode session",
        "fresh claude session",
        "fresh codex session",
    )
    return any(pattern in task_l for pattern in explicit_new_patterns)


def worker_supporting_resume_key(worker: str) -> str:
    value = (worker or "").strip().lower()
    return value if value in {"claude_code", "opencode"} else ""


def find_latest_worker_session_for_repo(
    *,
    worker: str,
    repo_path: str,
    list_sessions_fn: Callable[..., list[Any]],
) -> Any | None:
    wanted_worker = (worker or "").strip().lower()
    if wanted_worker not in {"claude_code", "opencode"}:
        return None
    try:
        repo_resolved = str(Path(repo_path).expanduser().resolve())
    except Exception:
        repo_resolved = str(repo_path)

    for record in list_sessions_fn(limit=100):
        if getattr(record, "status", "") == "cancelled":
            continue
        rec_worker = (
            (getattr(record, "selected_worker", "") or getattr(record, "requested_worker", ""))
            or ""
        ).strip().lower()
        if rec_worker != wanted_worker:
            continue
        try:
            rec_repo = str(Path(getattr(record, "repo_path", "")).expanduser().resolve())
        except Exception:
            rec_repo = str(getattr(record, "repo_path", ""))
        if rec_repo != repo_resolved:
            continue
        return record
    return None
