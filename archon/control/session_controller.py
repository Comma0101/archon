"""Session and delegation heuristics for control-plane orchestration."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from archon.setup.store import list_setup_records

_DEEP_SCOPE_SCOPE_PATTERNS = (
    re.compile(r"\brepo\b"),
    re.compile(r"\brepository\b"),
    re.compile(r"\bproject\b"),
    re.compile(r"\bcodebase\b"),
    re.compile(r"\bfolder\b"),
    re.compile(r"\bdirectory\b"),
)
_DEEP_SCOPE_BROAD_PATTERNS = (
    re.compile(r"\bdeep\b"),
    re.compile(r"\bcomprehensive\b"),
    re.compile(r"\bthorough\b"),
    re.compile(r"\bentire\b"),
    re.compile(r"\bwhole\b"),
    re.compile(r"\bfull\b"),
    re.compile(r"\bend[- ]to[- ]end\b"),
)
_DEEP_SCOPE_UNDERSTAND_PATTERNS = (
    re.compile(r"\bunderstand\b"),
    re.compile(r"\bmap out\b"),
    re.compile(r"\blearn\b"),
    re.compile(r"\breview architecture\b"),
    re.compile(r"\barchitecture review\b"),
)
_AI_NEWS_DIRECT_MARKERS = (
    "ai news",
    "artificial intelligence news",
    "news briefing",
    "news digest",
    "ai-news",
    "ai news briefing",
    "ai news digest",
)
_AI_NEWS_REFRESH_WORDS = {"refresh", "force", "refetch", "rebuild"}
_AI_NEWS_DELIVERY_WORDS = {"send", "post", "deliver", "share", "push", "forward"}
_JOB_REF_PATTERN = re.compile(
    r"\b(?P<kind>research|worker|call|setup):(?P<id>[A-Za-z0-9_-]+)\b",
    re.IGNORECASE,
)
_EXPLICIT_STATUS_PHRASES = (
    "status of",
    "status for",
    "progress of",
    "progress for",
    "state of",
    "state for",
)
_STATUS_NEGATION_MARKERS = ("cancel", "stop", "delete", "remove", "purge")
_JOBS_LIST_PHRASES = (
    "show active jobs",
    "show me active jobs",
    "list active jobs",
    "show running jobs",
    "list running jobs",
    "list jobs",
    "show jobs",
    "what jobs are running",
)
_EXPLICIT_NATIVE_SUBAGENT_PATTERN = re.compile(
    r"\b(?:use|spawn)\s+(?:a\s+)?native\s+"
    r"(?P<type>explore|general)\s+subagent\s+to\s+(?P<task>.+)",
    re.IGNORECASE | re.DOTALL,
)


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

    if is_broad_scope_request(task):
        return "background", "deep_scope_request"

    if int(timeout_sec) >= 1200 and mode_value in {"review", "analyze"}:
        return "background", "long_timeout_review"

    return "oneshot", "auto_small_task"


def is_broad_scope_request(task: str) -> bool:
    task_l = (task or "").lower()
    has_scope = _matches_any_pattern(task_l, _DEEP_SCOPE_SCOPE_PATTERNS)
    has_broad = _matches_any_pattern(task_l, _DEEP_SCOPE_BROAD_PATTERNS)
    has_understand = _matches_any_pattern(task_l, _DEEP_SCOPE_UNDERSTAND_PATTERNS)
    return has_scope and (has_broad or has_understand)


def is_ai_news_request(text: str) -> bool:
    compact = _normalize_text(text)
    if not compact or compact.startswith("/"):
        return False
    words = set(compact.split())
    if any(marker in compact for marker in _AI_NEWS_DIRECT_MARKERS):
        return True
    if "ai" in words and "news" in words:
        return bool({"briefing", "digest", "daily", "today", "latest", "update", "send", "brief"} & words)
    return False


def wants_news_force_refresh(text: str) -> bool:
    words = set(_normalize_text(text).split())
    return bool(_AI_NEWS_REFRESH_WORDS & words)


def wants_news_telegram_delivery(text: str) -> bool:
    compact = _normalize_text(text)
    words = set(compact.split())
    if "telegram" not in words:
        return False
    return bool(_AI_NEWS_DELIVERY_WORDS & words)


def extract_job_ref(text: str) -> str:
    match = _JOB_REF_PATTERN.search(str(text or ""))
    if not match:
        return ""
    kind = str(match.group("kind") or "").strip().lower()
    identifier = str(match.group("id") or "").strip()
    if not kind or not identifier:
        return ""
    return f"{kind}:{identifier}"


def match_blocked_setup_job_for_human_reply(
    text: str,
    *,
    list_records_fn: Callable[..., list[Any]] = list_setup_records,
) -> str:
    explicit_ref = extract_job_ref(text)
    kind, identifier = split_job_ref(explicit_ref)
    if kind == "setup" and identifier:
        return explicit_ref

    compact = _normalize_text(text)
    if not compact or compact.startswith("/"):
        return ""

    matches: list[str] = []
    for record in list_records_fn(limit=20):
        if str(getattr(record, "status", "") or "").strip().lower() != "blocked":
            continue
        if _blocked_setup_match_score(record, compact) <= 0:
            continue
        setup_id = str(getattr(record, "setup_id", "") or "").strip()
        if setup_id:
            matches.append(setup_id)

    if len(matches) != 1:
        return ""
    return f"setup:{matches[0]}"


def split_job_ref(job_ref: str) -> tuple[str, str]:
    ref = str(job_ref or "").strip()
    if ":" not in ref:
        return "", ""
    kind, identifier = ref.split(":", 1)
    return kind.strip().lower(), identifier.strip()


def extract_research_job_id(text: str) -> str:
    ref = extract_job_ref(text)
    kind, identifier = split_job_ref(ref)
    if kind != "research":
        return ""
    return f"{kind}:{identifier}"


def extract_explicit_job_status_ref(text: str) -> str:
    compact = _normalize_text(text)
    if not compact or compact.startswith("/"):
        return ""
    if any(marker in compact for marker in _STATUS_NEGATION_MARKERS):
        return ""
    ref = extract_job_ref(text)
    if not ref:
        return ""
    if not any(phrase in compact for phrase in _EXPLICIT_STATUS_PHRASES):
        return ""
    return ref


def extract_explicit_native_subagent_request(text: str) -> tuple[str, str]:
    raw = str(text or "").strip()
    if not raw or raw.startswith("/"):
        return "", ""
    match = _EXPLICIT_NATIVE_SUBAGENT_PATTERN.search(raw)
    if not match:
        return "", ""
    subagent_type = str(match.group("type") or "").strip().lower()
    task = str(match.group("task") or "").strip()
    if not subagent_type or not task:
        return "", ""
    return subagent_type, task


def is_explicit_research_status_request(text: str) -> bool:
    return extract_explicit_job_status_ref(text).startswith("research:")


def is_explicit_job_list_request(text: str) -> bool:
    compact = _normalize_text(text)
    if not compact or compact.startswith("/"):
        return False
    if extract_job_ref(text):
        return False
    return any(phrase in compact for phrase in _JOBS_LIST_PHRASES)


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


def _matches_any_pattern(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def _normalize_text(text: str) -> str:
    compact = re.sub(r"[^a-z0-9\s'’]", " ", str(text or "").lower())
    compact = compact.replace("’", " ").strip()
    return re.sub(r"\s+", " ", compact)


def _blocked_setup_match_score(record: Any, compact_text: str) -> int:
    score = 0
    project_name = _normalize_text(getattr(record, "project_name", ""))
    setup_id = _normalize_text(getattr(record, "setup_id", ""))
    if project_name and project_name in compact_text:
        score += 3
    if setup_id and setup_id in compact_text:
        score += 3
    for item in getattr(record, "blocked_on", []) or []:
        if not isinstance(item, dict):
            continue
        env_var = _normalize_text(str(item.get("env_var", "") or ""))
        what = _normalize_text(str(item.get("what", "") or ""))
        if env_var and env_var in compact_text:
            score += 2
        elif what and what in compact_text:
            score += 1
    return score
