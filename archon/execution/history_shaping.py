"""Shared tool-result history shaping helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping

from archon.security.redaction import redact_secret_like_text


_SHELL_EXIT_CODE_RE = re.compile(r"\n?\[exit_code=(-?\d+)\]\s*$")
_WORKER_TOOL_NAMES = {
    "delegate_code_task",
    "worker_start",
    "worker_send",
    "worker_status",
    "worker_poll",
    "worker_list",
}
_HISTORY_SAMPLE_TOOLS = {"list_dir", "glob", "grep"}


def shape_tool_result_for_history(
    tool_name: str,
    tool_args: Mapping[str, object],
    result_text: str,
    *,
    tool_result_max_chars: int,
    tool_result_worker_max_chars: int,
) -> str:
    name = str(tool_name or "").strip().lower()
    if name in _WORKER_TOOL_NAMES:
        return truncate_text_for_history(
            result_text,
            min(tool_result_max_chars, tool_result_worker_max_chars),
        )
    if name == "shell":
        return shape_shell_result_for_history(tool_args, result_text, tool_result_max_chars=tool_result_max_chars)
    if name == "read_file":
        return shape_read_file_result_for_history(tool_args, result_text, tool_result_max_chars=tool_result_max_chars)
    if name in _HISTORY_SAMPLE_TOOLS:
        return shape_sampled_result_for_history(
            name,
            tool_args,
            result_text,
            tool_result_max_chars=tool_result_max_chars,
        )
    return truncate_text_for_history(result_text, tool_result_max_chars)


def shape_shell_result_for_history(
    tool_args: Mapping[str, object],
    result_text: str,
    *,
    tool_result_max_chars: int,
) -> str:
    body, exit_code = split_shell_exit_code(result_text)
    excerpt = summarize_lines_with_head_tail(body or "(no output)", head=6, tail=4)
    command = redact_secret_like_text(str(tool_args.get("command", "") or ""))
    lines: list[str] = []
    if command:
        lines.append(f"command: {command}")
    if exit_code is not None:
        lines.append(f"exit_code: {exit_code}")
    lines.extend(["output:", excerpt])
    return truncate_text_for_history("\n".join(lines), tool_result_max_chars)


def shape_read_file_result_for_history(
    tool_args: Mapping[str, object],
    result_text: str,
    *,
    tool_result_max_chars: int,
) -> str:
    lines = [
        f"path: {redact_secret_like_text(str(tool_args.get('path', '') or ''))}",
        f"offset: {int(tool_args.get('offset', 0) or 0)}",
        f"limit: {int(tool_args.get('limit', 2000) or 2000)}",
        "excerpt:",
        summarize_lines_head_only(result_text or "(empty file)", head=12),
    ]
    return truncate_text_for_history("\n".join(lines), tool_result_max_chars)


def shape_sampled_result_for_history(
    tool_name: str,
    tool_args: Mapping[str, object],
    result_text: str,
    *,
    tool_result_max_chars: int,
) -> str:
    lines: list[str] = []
    if tool_name == "list_dir":
        lines.append(f"path: {redact_secret_like_text(str(tool_args.get('path', '.') or '.'))}")
        label = "entries"
    elif tool_name == "glob":
        lines.append(f"root: {redact_secret_like_text(str(tool_args.get('root', '.') or '.'))}")
        lines.append(f"pattern: {str(tool_args.get('pattern', '') or '')}")
        label = "matches"
    else:
        lines.append(f"root: {redact_secret_like_text(str(tool_args.get('root', '.') or '.'))}")
        lines.append(f"pattern: {str(tool_args.get('pattern', '') or '')}")
        glob_value = str(tool_args.get("glob", "") or "").strip()
        if glob_value:
            lines.append(f"glob: {glob_value}")
        label = "matches"
    lines.append(f"{label}: {count_result_items(result_text)}")
    lines.extend(["sample:", summarize_lines_head_only(result_text or "(no matches)", head=8)])
    return truncate_text_for_history("\n".join(lines), tool_result_max_chars)


def truncate_text_for_history(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    omitted = len(text) - limit
    head_size = int(limit * 0.65)
    tail_size = int(limit * 0.25)
    middle = f"\n... [{omitted} chars omitted] ...\n"
    if head_size + tail_size + len(middle) >= len(text):
        return text
    return text[:head_size] + middle + text[-tail_size:]


def summarize_lines_head_only(text: str, *, head: int) -> str:
    lines = text.splitlines()
    if len(lines) <= head:
        return text
    omitted = len(lines) - head
    return "\n".join(lines[:head] + [f"... [{omitted} lines omitted] ..."])


def summarize_lines_with_head_tail(text: str, *, head: int, tail: int) -> str:
    lines = text.splitlines()
    if len(lines) <= head + tail:
        return text
    omitted = len(lines) - head - tail
    return "\n".join(lines[:head] + [f"... [{omitted} lines omitted] ..."] + lines[-tail:])


def split_shell_exit_code(result_text: str) -> tuple[str, int | None]:
    match = _SHELL_EXIT_CODE_RE.search(result_text)
    if not match:
        return result_text, None
    body = result_text[:match.start()].rstrip("\n")
    try:
        exit_code = int(match.group(1))
    except (TypeError, ValueError):
        exit_code = None
    return body, exit_code


def count_result_items(result_text: str) -> int:
    stripped = result_text.strip()
    if not stripped or stripped in {"(no matches)", "(empty directory)"}:
        return 0
    count = 0
    for line in result_text.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        if cleaned.startswith("... (") and cleaned.endswith("more entries)"):
            continue
        if cleaned.startswith("... (") and cleaned.endswith("more lines)"):
            continue
        count += 1
    return count
