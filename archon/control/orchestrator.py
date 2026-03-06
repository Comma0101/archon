"""Feature-flagged orchestration wrapper with legacy-safe fallback."""

from __future__ import annotations

from typing import Callable, TypeVar

from archon.control.contracts import RouteDecision


T = TypeVar("T")


def orchestrate_response(
    *,
    mode: str,
    turn_id: str,
    run_legacy: Callable[[], T],
    emit_hook: Callable[[str, dict], None] | None = None,
) -> T:
    """Route a non-streaming LLM step through legacy or hybrid orchestration."""
    normalized_mode = _normalize_mode(mode)
    if normalized_mode != "hybrid":
        _emit(
            emit_hook,
            "orchestrator.route",
            _route_payload(turn_id=turn_id, mode="legacy", path="legacy_direct"),
        )
        return run_legacy()

    try:
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
    run_legacy_stream: Callable[[], T],
    emit_hook: Callable[[str, dict], None] | None = None,
) -> T:
    """Route a streaming LLM step through legacy or hybrid orchestration."""
    normalized_mode = _normalize_mode(mode)
    if normalized_mode != "hybrid":
        _emit(
            emit_hook,
            "orchestrator.route",
            _route_payload(turn_id=turn_id, mode="legacy", path="legacy_stream_direct"),
        )
        return run_legacy_stream()

    try:
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
    _emit(
        emit_hook,
        "orchestrator.route",
        _route_payload(turn_id=turn_id, mode="hybrid", path="hybrid_planner_v0"),
    )
    # Phase 3 keeps behavior parity by using legacy execution after routing.
    return run_legacy()


def _run_hybrid_stream_response(
    *,
    turn_id: str,
    run_legacy_stream: Callable[[], T],
    emit_hook: Callable[[str, dict], None] | None,
) -> T:
    _emit(
        emit_hook,
        "orchestrator.route",
        _route_payload(turn_id=turn_id, mode="hybrid", path="hybrid_stream_planner_v0"),
    )
    # Phase 3 keeps behavior parity by using legacy streaming execution after routing.
    return run_legacy_stream()


def _normalize_mode(mode: str) -> str:
    value = (mode or "legacy").strip().lower()
    if value == "hybrid":
        return "hybrid"
    return "legacy"


def _route_payload(*, turn_id: str, mode: str, path: str) -> dict:
    decision = RouteDecision(turn_id=turn_id, mode=mode, path=path)
    return {
        "turn_id": decision.turn_id,
        "mode": decision.mode,
        "path": decision.path,
        "lane": decision.lane,
        "reason": decision.reason,
        "surface": decision.surface,
        "skill": decision.skill,
    }


def _emit(emit_hook: Callable[[str, dict], None] | None, kind: str, payload: dict) -> None:
    if emit_hook is None:
        return
    try:
        emit_hook(kind, payload)
    except Exception:
        return
