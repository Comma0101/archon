"""Feature-flagged orchestration wrapper with legacy-safe fallback."""

from __future__ import annotations

import re
from typing import Callable, TypeVar

from archon.control.contracts import RouteDecision
from archon.control.session_controller import is_broad_scope_request


T = TypeVar("T")
_PATH_LIKE_PATTERN = re.compile(
    r"(?:^|[\s`'\"])(?:\.{0,2}/)?[\w./-]+\.(?:py|md|txt|json|ya?ml|toml|ini|cfg|js|ts|tsx|jsx|sh)\b"
)
_OPERATOR_STATUS_PATTERNS = (
    re.compile(r"\bgit\s+status\b"),
    re.compile(r"\b(?:worker|session|run|job|branch)\s+status\b"),
    re.compile(r"\bstatus\s+of\s+(?:the\s+)?(?:worker|session|run|job|branch|repo|repository)\b"),
    re.compile(r"\bdiff\b"),
    re.compile(r"\bwhat\s+changed\b"),
    re.compile(r"\bchanged\s+files?\b"),
    re.compile(r"\blist\s+files?\b"),
    re.compile(r"\bls\b"),
    re.compile(r"\bpwd\b"),
)
_OPERATOR_FILE_ACTION_PATTERN = re.compile(r"\b(?:show|read|open|cat|tail|head|print|display)\b")
_OPERATOR_FILE_TARGET_PATTERN = re.compile(
    r"\b(?:file|files|path|directory|folder|repo|repository)\b|\bcontents\s+of\b"
)
_JOB_DELEGATE_PATTERNS = (
    re.compile(r"\bdelegat(?:e|ing)\b"),
    re.compile(r"\bhand(?:\s+(?:this|that|it|the\s+\w+))?\s+off\b"),
    re.compile(r"\bspin\s+up\b"),
    re.compile(r"\bsubagent\b"),
    re.compile(r"\bparallelize\b"),
    re.compile(r"\bbackground\s+this\b"),
    re.compile(r"\bin\s+the\s+background\b"),
    re.compile(r"\brun\s+(?:this|that|it)\s+in\s+(?:the\s+)?background\b"),
    re.compile(r"\bstart\s+a\s+new\s+session\b"),
    re.compile(r"\bstart\s+new\s+session\b"),
    re.compile(r"\bfresh\s+session\b"),
)
_JOB_NEGATED_DELEGATE_PATTERNS = (
    re.compile(r"\bdo\s+not\s+delegate\b"),
    re.compile(r"\bdon'?t\s+delegate\b"),
    re.compile(r"\bdon'?t\s+want\s+to\s+delegate\b"),
    re.compile(r"\bnot\s+delegate(?:\b|\s)"),
    re.compile(r"\bwithout\s+delegat(?:e|ing)\b"),
)
_DEEP_RESEARCH_INTENT_MARKERS = (
    "research",
    "analyze",
    "synthesize",
    "compare",
    "evaluate",
)
_DEEP_RESEARCH_SCOPE_MARKERS = (
    "market",
    "markets",
    "competitor",
    "competitors",
    "landscape",
    "industry",
    "space",
    "due diligence",
    "literature",
    "report",
)


def orchestrate_response(
    *,
    mode: str,
    turn_id: str,
    user_message: str = "",
    run_legacy: Callable[[], T],
    emit_hook: Callable[[str, dict], None] | None = None,
) -> T:
    """Route a non-streaming LLM step through legacy or hybrid orchestration."""
    normalized_mode = _normalize_mode(mode)
    lane, reason = _classify_route(user_message)
    if normalized_mode != "hybrid":
        _emit(
            emit_hook,
            "orchestrator.route",
            _route_payload(
                turn_id=turn_id,
                mode="legacy",
                path="legacy_direct",
                lane=lane,
                reason=reason,
            ),
        )
        return run_legacy()

    try:
        _emit(
            emit_hook,
            "orchestrator.route",
            _route_payload(
                turn_id=turn_id,
                mode="hybrid",
                path="hybrid_planner_v0",
                lane=lane,
                reason=reason,
            ),
        )
        return _run_hybrid_response(
            turn_id=turn_id,
            run_legacy=run_legacy,
            emit_hook=emit_hook,
        )
    except Exception as e:
        _emit(
            emit_hook,
            "orchestrator.fallback",
            {
                "turn_id": turn_id,
                "mode": "hybrid",
                "fallback": "legacy",
                "error_type": type(e).__name__,
                "error": str(e),
            },
        )
        return run_legacy()


def orchestrate_stream_response(
    *,
    mode: str,
    turn_id: str,
    user_message: str = "",
    run_legacy_stream: Callable[[], T],
    emit_hook: Callable[[str, dict], None] | None = None,
) -> T:
    """Route a streaming LLM step through legacy or hybrid orchestration."""
    normalized_mode = _normalize_mode(mode)
    lane, reason = _classify_route(user_message)
    if normalized_mode != "hybrid":
        _emit(
            emit_hook,
            "orchestrator.route",
            _route_payload(
                turn_id=turn_id,
                mode="legacy",
                path="legacy_stream_direct",
                lane=lane,
                reason=reason,
            ),
        )
        return run_legacy_stream()

    try:
        _emit(
            emit_hook,
            "orchestrator.route",
            _route_payload(
                turn_id=turn_id,
                mode="hybrid",
                path="hybrid_stream_planner_v0",
                lane=lane,
                reason=reason,
            ),
        )
        return _run_hybrid_stream_response(
            turn_id=turn_id,
            run_legacy_stream=run_legacy_stream,
            emit_hook=emit_hook,
        )
    except Exception as e:
        _emit(
            emit_hook,
            "orchestrator.fallback",
            {
                "turn_id": turn_id,
                "mode": "hybrid",
                "fallback": "legacy_stream",
                "error_type": type(e).__name__,
                "error": str(e),
            },
        )
        return run_legacy_stream()


def _run_hybrid_response(
    *,
    turn_id: str,
    run_legacy: Callable[[], T],
    emit_hook: Callable[[str, dict], None] | None,
) -> T:
    # Phase 3 keeps behavior parity by using legacy execution after routing.
    return run_legacy()


def _run_hybrid_stream_response(
    *,
    turn_id: str,
    run_legacy_stream: Callable[[], T],
    emit_hook: Callable[[str, dict], None] | None,
) -> T:
    # Phase 3 keeps behavior parity by using legacy streaming execution after routing.
    return run_legacy_stream()


def _normalize_mode(mode: str) -> str:
    value = (mode or "legacy").strip().lower()
    if value == "hybrid":
        return "hybrid"
    return "legacy"


def _route_payload(
    *,
    turn_id: str,
    mode: str,
    path: str,
    lane: str = "operator",
    reason: str = "static_default_until_classifier",
) -> dict:
    decision = RouteDecision(turn_id=turn_id, mode=mode, path=path, lane=lane, reason=reason)
    return {
        "turn_id": decision.turn_id,
        "mode": decision.mode,
        "path": decision.path,
        "lane": decision.lane,
        "reason": decision.reason,
        "surface": decision.surface,
        "skill": decision.skill,
    }


def build_route_payload(
    *,
    turn_id: str,
    mode: str,
    path: str,
    lane: str = "operator",
    reason: str = "static_default_until_classifier",
) -> dict:
    return _route_payload(
        turn_id=turn_id,
        mode=mode,
        path=path,
        lane=lane,
        reason=reason,
    )


def classify_route(user_message: str) -> tuple[str, str]:
    return _classify_route(user_message)


def is_deep_research_request(user_message: str) -> bool:
    text_l = (user_message or "").strip().lower()
    if not text_l:
        return False

    normalized = re.sub(r'[^\w\s]', '', text_l).strip()
    if normalized in (
        "can you do deep research",
        "do you do deep research",
        "can you do a deep research",
        "how do i use deep research",
        "what is deep research",
        "can we do deep research",
        "do deep research",
        "deep research",
        "can you do deep research for me",
    ):
        return False

    if any(
        phrase in text_l
        for phrase in ("deep research", "deeply research", "research this deeply")
    ):
        return True
    has_research_intent = any(marker in text_l for marker in _DEEP_RESEARCH_INTENT_MARKERS)
    has_scope_hint = any(marker in text_l for marker in _DEEP_RESEARCH_SCOPE_MARKERS)
    return has_research_intent and has_scope_hint


def _classify_route(user_message: str) -> tuple[str, str]:
    text = (user_message or "").strip()
    if not text:
        return "fast", "simple_chat"

    text_l = text.lower()
    if _is_operator_request(text, text_l):
        return "operator", "bounded_file_or_status_request"
    if is_deep_research_request(text_l):
        return "job", "deep_research_request"
    if _is_job_request(text_l):
        return "job", "broad_or_delegated_request"
    return "fast", "simple_chat"


def _is_job_request(text_l: str) -> bool:
    has_negated_delegate = _matches_any_pattern(text_l, _JOB_NEGATED_DELEGATE_PATTERNS)
    if not has_negated_delegate and _matches_any_pattern(text_l, _JOB_DELEGATE_PATTERNS):
        return True
    return is_broad_scope_request(text_l)


def _is_operator_request(text: str, text_l: str) -> bool:
    if _matches_any_pattern(text_l, _OPERATOR_STATUS_PATTERNS):
        return True

    has_file_action = bool(_OPERATOR_FILE_ACTION_PATTERN.search(text_l))
    has_file_target = bool(_OPERATOR_FILE_TARGET_PATTERN.search(text_l)) or bool(
        _PATH_LIKE_PATTERN.search(text)
    )
    return has_file_action and has_file_target


def _matches_any_pattern(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def _emit(emit_hook: Callable[[str, dict], None] | None, kind: str, payload: dict) -> None:
    if emit_hook is None:
        return
    try:
        emit_hook(kind, payload)
    except Exception:
        return
