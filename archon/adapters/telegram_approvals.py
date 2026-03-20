"""Small Telegram approval UX helpers (pure functions, no adapter state)."""

from __future__ import annotations

from typing import Any

from archon.ux.operator_messages import (
    build_approval_status_message,
    build_blocked_action_message,
)


PENDING_APPROVAL_TTL_SEC = 5 * 60
ELEVATED_APPROVAL_TTL_SEC = 15 * 60
APPROVAL_COMMAND_PREVIEW_LIMIT = 240

APPROVAL_CALLBACK_PREFIX = "appr"
APPROVAL_ACTION_APPROVE = "approve"
APPROVAL_ACTION_ALLOW15 = "allow15"
APPROVAL_ACTION_DENY = "deny"


def truncate_approval_command(command: str, limit: int = APPROVAL_COMMAND_PREVIEW_LIMIT) -> str:
    command = (command or "").strip()
    if len(command) <= limit:
        return command
    return command[: limit - 1] + "…"


def build_pending_approval_text(command_preview: str) -> str:
    return (
        build_blocked_action_message(
            command_preview,
            heading="Dangerous action blocked and needs approval.",
        )
        + "\n"
        + "inline_allow=Allow 15m"
    )


def build_approval_status_text(command_preview: str, status_text: str) -> str:
    return build_approval_status_message(
        command_preview,
        status_text,
    )


def build_approval_reply_markup(approval_id: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "Approve Request",
                    "callback_data": f"{APPROVAL_CALLBACK_PREFIX}:{approval_id}:{APPROVAL_ACTION_APPROVE}",
                },
                {
                    "text": "Allow 15m",
                    "callback_data": f"{APPROVAL_CALLBACK_PREFIX}:{approval_id}:{APPROVAL_ACTION_ALLOW15}",
                },
            ],
            [
                {
                    "text": "Deny",
                    "callback_data": f"{APPROVAL_CALLBACK_PREFIX}:{approval_id}:{APPROVAL_ACTION_DENY}",
                },
            ],
        ]
    }


def parse_approval_callback_data(data: str | None) -> tuple[str, str] | None:
    if not isinstance(data, str) or not data.startswith(f"{APPROVAL_CALLBACK_PREFIX}:"):
        return None
    parts = data.split(":", 2)
    if len(parts) != 3:
        return None
    _, approval_id, action = parts
    if not approval_id or action not in {
        APPROVAL_ACTION_APPROVE,
        APPROVAL_ACTION_ALLOW15,
        APPROVAL_ACTION_DENY,
    }:
        return None
    return approval_id, action


def looks_like_safety_gate_rejection(response: str | None) -> bool:
    if not isinstance(response, str):
        return False
    lowered = response.lower()
    return (
        "rejected by safety gate" in lowered
        or "self-modification rejected" in lowered
        or lowered.startswith("forbidden:")
    )


def answer_callback_query_safe(
    bot: Any,
    callback_query_id: str,
    *,
    text: str | None = None,
    show_alert: bool = False,
) -> None:
    """Best-effort callback answer wrapper to avoid repeated try/except boilerplate."""
    try:
        bot.answer_callback_query(
            callback_query_id,
            text=text,
            show_alert=show_alert,
        )
    except Exception:
        pass
