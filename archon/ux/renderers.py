"""Shared rendering helpers for tool execution feedback."""

from __future__ import annotations

import re

_SHELL_EXIT_RE = re.compile(r"\[exit_code=(-?\d+)\]\s*$")


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    if count == 1:
        return singular
    return plural or f"{singular}s"


def build_tool_summary(tool_name: str, meta: dict, result_str: str) -> str:
    """Build a compact one-line summary from metadata or fallback parsing."""
    if tool_name == "shell":
        exit_code = meta.get("exit_code")
        line_count = meta.get("line_count")
        if exit_code is not None and line_count is not None:
            return f"shell: exit {exit_code} ({line_count} lines)"
        match = _SHELL_EXIT_RE.search(result_str)
        if match:
            exit_code = int(match.group(1))
            body = result_str[:match.start()].strip()
            line_count = len(body.splitlines()) if body else 0
            return f"shell: exit {exit_code} ({line_count} {_plural(line_count, 'line')})"

    if tool_name == "read_file":
        path = meta.get("path")
        line_count = meta.get("line_count")
        if path is not None and line_count is not None:
            return f"read: {path} ({line_count} lines)"

    if tool_name == "edit_file":
        path = meta.get("path")
        line_number = meta.get("line_number")
        lines_changed = meta.get("lines_changed")
        if path is not None and lines_changed is not None:
            location = f":{line_number}" if line_number else ""
            return f"edit: {path}{location} ({lines_changed} {_plural(lines_changed, 'line changed')})"

    if tool_name == "write_file":
        path = meta.get("path")
        line_count = meta.get("line_count")
        is_new = meta.get("is_new")
        if path is not None and line_count is not None:
            prefix = "new, " if is_new else ""
            return f"write: {path} ({prefix}{line_count} lines)"

    if tool_name == "grep":
        pattern = meta.get("pattern")
        match_count = meta.get("match_count")
        file_count = meta.get("file_count")
        if pattern is not None and match_count is not None and file_count is not None:
            return f"grep: '{pattern}' -> {match_count} matches in {file_count} files"

    if tool_name == "glob":
        pattern = meta.get("pattern")
        file_count = meta.get("file_count")
        if pattern is not None and file_count is not None:
            return f"glob: {pattern} -> {file_count} files"

    return f"{tool_name}: done"


def collapse_output_lines(
    lines: list[str],
    max_lines: int = 20,
    head: int = 8,
    tail: int = 5,
) -> list[str]:
    """Collapse long output into head + elision + tail."""
    if len(lines) <= max_lines:
        return lines
    head = max(0, head)
    tail = max(0, tail)
    if head + tail >= len(lines):
        return lines
    hidden = len(lines) - head - tail
    return lines[:head] + [f"... ({hidden} more lines)"] + lines[-tail:]


def truncate_diff_lines(lines: list[str], max_lines: int = 10) -> list[str]:
    """Truncate diff display with an explicit omitted-line notice."""
    if len(lines) <= max_lines:
        return lines
    remaining = len(lines) - max_lines
    return lines[:max_lines] + [f"... ({remaining} more lines changed)"]
