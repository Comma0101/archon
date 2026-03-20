"""Shared operator-facing message builders."""

from __future__ import annotations


def _build_pipe_message(prefix: str, *fields: tuple[str, object]) -> str:
    rendered = " | ".join(f"{key}={value}" for key, value in fields if value is not None)
    return f"{prefix}: {rendered}" if rendered else f"{prefix}:"


def _normalize_request_preview(command_preview: str | None, *, empty: str) -> str:
    preview = str(command_preview or "").strip()
    return preview or empty


def build_compact_result_text(
    *,
    compacted_messages: int,
    path: str,
    pending_compactions: int,
) -> str:
    message = (
        f"Compact: history_messages={max(0, int(compacted_messages or 0))} | "
        f"path={str(path or '').strip()} | "
        f"pending_compactions={max(0, int(pending_compactions or 0))}"
    )
    if str(path or "").strip():
        message += " | next_turn=uses_compacted_context"
    return message


def build_fresh_start_text(*, cleared_messages: int) -> str:
    return (
        f"Cleared {max(0, int(cleared_messages or 0))} messages. "
        "Fresh chat context in the same session."
    )


def build_pressure_recommendation(pressure: str | None) -> str:
    level = str(pressure or "").strip().lower()
    if level == "high":
        return "/compact or /new"
    if level == "warn":
        return "/compact"
    return ""


def build_blocked_action_message(
    command_preview: str,
    *,
    replay_command: str = "/approve",
    allow_once_command: str = "/approve_next",
    deny_command: str = "/deny",
    review_command: str = "/approvals",
    replay_effect: str = "replays_pending_request",
    allow_once_effect: str = "arms_one_future_dangerous_action",
    extra_lines: tuple[str, ...] = (),
    heading: str = "Dangerous action blocked and needs approval.",
) -> str:
    preview = str(command_preview or "").strip() or "(unknown command)"
    lines = [
        f"{heading}\n\n"
        f"pending_request={preview}",
        f"replay={replay_command}",
        f"replay_effect={replay_effect}",
        f"allow_once={allow_once_command}",
        f"allow_once_effect={allow_once_effect}",
        f"deny={deny_command}",
        f"review={review_command}",
    ]
    lines.extend(str(line).strip() for line in extra_lines if str(line).strip())
    return "\n".join(lines)


def build_approvals_overview_message(
    *,
    dangerous_mode: bool,
    pending_request: str | None,
    allow_once_remaining: int,
    replay_command: str = "/approve",
    allow_once_command: str = "/approve_next",
    deny_command: str = "/deny",
    result: str | None = None,
    elevated_ttl_sec: int | None = None,
) -> str:
    fields: list[tuple[str, object]] = []
    if result:
        fields.append(("result", result))
    fields.extend(
        [
            ("dangerous_mode", "on" if dangerous_mode else "off"),
            ("pending_request", _normalize_request_preview(pending_request, empty="none")),
            ("allow_once_remaining", max(0, int(allow_once_remaining or 0))),
        ]
    )
    if elevated_ttl_sec is not None and int(elevated_ttl_sec) > 0:
        fields.append(("elevated_ttl_sec", max(0, int(elevated_ttl_sec))))
    fields.extend(
        [
            ("replay", replay_command),
            ("allow_once", allow_once_command),
            ("deny", deny_command),
        ]
    )
    return _build_pipe_message("Approvals", *fields)


def build_approval_result_message(
    *,
    result: str | None = None,
    state: str | None = None,
    requested: str | None = None,
    pending_request: str | None = None,
    replayed_request: str | None = None,
    denied_request: str | None = None,
    dangerous_mode: bool | None = None,
    allow_once_remaining: int | None = None,
    next_step: str | None = None,
    replay_command: str = "/approve",
    allow_once_command: str = "/approve_next",
    deny_command: str = "/deny",
    review_command: str = "/approvals",
) -> str:
    fields: list[tuple[str, object]] = []
    if result:
        fields.append(("result", result))
    if state:
        fields.append(("state", state))
    if requested:
        fields.append(("requested", requested))
    if replayed_request is not None:
        fields.append(("replayed_request", _normalize_request_preview(replayed_request, empty="none")))
    if denied_request is not None:
        fields.append(("denied_request", _normalize_request_preview(denied_request, empty="none")))
    if dangerous_mode is not None:
        fields.append(("dangerous_mode", "on" if dangerous_mode else "off"))
    if pending_request is not None:
        fields.append(("pending_request", _normalize_request_preview(pending_request, empty="none")))
    if allow_once_remaining is not None:
        fields.append(("allow_once_remaining", max(0, int(allow_once_remaining or 0))))
    if next_step:
        fields.append(("next", next_step))
    if result == "no_pending_request":
        fields.extend(
            [
                ("replay", replay_command),
                ("allow_once", allow_once_command),
                ("deny", deny_command),
            ]
        )
    fields.append(("review", review_command))
    return _build_pipe_message("Approval", *fields)


def build_approval_status_message(
    command_preview: str,
    status_text: str,
    *,
    review_command: str = "/approvals",
) -> str:
    preview = str(command_preview or "").strip() or "(unknown command)"
    status = str(status_text or "").strip() or "unknown"
    return (
        "Dangerous action approval\n\n"
        f"status={status}\n"
        f"pending_request={preview}\n"
        f"review={review_command}"
    )


def build_operator_help_workflows() -> str:
    return (
        "Inspect: /status, /context\n"
        "Recover: /compact, /new\n"
        "Approvals: /approvals, /approve, /approve_next, /deny"
    )
