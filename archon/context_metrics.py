"""Lightweight context usage snapshots for local operator surfaces."""

from __future__ import annotations

from dataclasses import dataclass


APPROX_CHARS_PER_TOKEN = 4
PROMPT_PRESSURE_WARN_INPUT_TOKENS = 10000
PROMPT_PRESSURE_HIGH_INPUT_TOKENS = 20000
PROMPT_PRESSURE_WARN_HISTORY_TOKENS = 6000
PROMPT_PRESSURE_HIGH_HISTORY_TOKENS = 12000


@dataclass(frozen=True)
class ContextSnapshot:
    history_messages: int
    history_chars: int
    approx_history_tokens: int
    pending_compactions: int
    visible_tool_count: int
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    last_input_tokens: int
    last_output_tokens: int
    pressure: str


def build_context_snapshot(agent) -> ContextSnapshot:
    history = list(getattr(agent, "history", []) or [])
    history_chars = estimate_history_chars(history)
    approx_history_tokens = estimate_tokens_from_chars(history_chars)
    pending_compactions = len(getattr(agent, "_pending_compactions", []) or [])
    visible_tool_count = _visible_tool_count(agent)
    total_input_tokens = max(0, int(getattr(agent, "total_input_tokens", 0) or 0))
    total_output_tokens = max(0, int(getattr(agent, "total_output_tokens", 0) or 0))
    last_input_tokens = max(0, int(getattr(agent, "last_input_tokens", 0) or 0))
    last_output_tokens = max(0, int(getattr(agent, "last_output_tokens", 0) or 0))
    return ContextSnapshot(
        history_messages=len(history),
        history_chars=history_chars,
        approx_history_tokens=approx_history_tokens,
        pending_compactions=pending_compactions,
        visible_tool_count=visible_tool_count,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        total_tokens=total_input_tokens + total_output_tokens,
        last_input_tokens=last_input_tokens,
        last_output_tokens=last_output_tokens,
        pressure=pressure_label_for_agent(
            agent,
            last_input_tokens=last_input_tokens,
            approx_history_tokens=approx_history_tokens,
        ),
    )


def estimate_tokens_from_chars(char_count: int) -> int:
    value = max(0, int(char_count or 0))
    if value == 0:
        return 0
    return max(1, (value + APPROX_CHARS_PER_TOKEN - 1) // APPROX_CHARS_PER_TOKEN)


def estimate_history_chars(history: list[dict]) -> int:
    return max(0, sum(_estimate_message_chars(message) for message in history))


def _estimate_message_chars(message: object) -> int:
    """Approximate serialized payload size using JSON-like recursion."""
    if message is None:
        return 0
    if isinstance(message, str):
        return len(message)
    if isinstance(message, (int, float, bool)):
        return 8
    if isinstance(message, list):
        return sum(_estimate_message_chars(item) for item in message)
    if isinstance(message, dict):
        total = 0
        for key, value in message.items():
            if key == "_provider_message":
                total += 16
                continue
            total += len(str(key))
            total += _estimate_message_chars(value)
        return total
    return len(str(message))


def _pressure_label(*, last_input_tokens: int, approx_history_tokens: int) -> str:
    if (
        last_input_tokens >= PROMPT_PRESSURE_HIGH_INPUT_TOKENS
        or approx_history_tokens >= PROMPT_PRESSURE_HIGH_HISTORY_TOKENS
    ):
        return "high"
    if (
        last_input_tokens >= PROMPT_PRESSURE_WARN_INPUT_TOKENS
        or approx_history_tokens >= PROMPT_PRESSURE_WARN_HISTORY_TOKENS
    ):
        return "warn"
    return "ok"


def pressure_label_for_agent(agent, *, last_input_tokens: int, approx_history_tokens: int) -> str:
    cfg = getattr(agent, "config", None)
    agent_cfg = getattr(cfg, "agent", None) if cfg is not None else None
    input_high = _coerce_threshold(
        getattr(agent_cfg, "prompt_pressure_max_input_tokens", None),
        PROMPT_PRESSURE_HIGH_INPUT_TOKENS,
    )
    history_high = _coerce_threshold(
        getattr(agent_cfg, "prompt_pressure_max_history_tokens", None),
        PROMPT_PRESSURE_HIGH_HISTORY_TOKENS,
    )
    input_warn = max(1, input_high // 2) if input_high > 0 else PROMPT_PRESSURE_WARN_INPUT_TOKENS
    history_warn = max(1, history_high // 2) if history_high > 0 else 0

    if (
        input_high > 0 and last_input_tokens >= input_high
    ) or (
        history_high > 0 and approx_history_tokens >= history_high
    ):
        return "high"
    if (
        input_warn > 0 and last_input_tokens >= input_warn
    ) or (
        history_warn > 0 and approx_history_tokens >= history_warn
    ):
        return "warn"
    return "ok"


def _coerce_threshold(value: object, default: int) -> int:
    if value is None:
        return default
    if not isinstance(value, (int, float, str)):
        return default
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _visible_tool_count(agent) -> int:
    tools = getattr(agent, "tools", None)
    if tools is None:
        return 0
    cfg = getattr(agent, "config", None)
    if cfg is not None:
        getter = getattr(tools, "get_schemas_for_profile", None)
        if callable(getter):
            profile_name = str(getattr(agent, "policy_profile", "") or "default").strip() or "default"
            try:
                return len(getter(cfg, profile_name=profile_name))
            except Exception:
                pass
    getter = getattr(tools, "get_schemas", None)
    if callable(getter):
        try:
            return len(getter())
        except Exception:
            return 0
    return 0
