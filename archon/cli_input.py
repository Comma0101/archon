"""Paste-input helper functions for Archon CLI."""

from __future__ import annotations


PASTE_END_MARKERS = {"/end", ".end", ":end"}
BRACKETED_PASTE_START = "\x1b[200~"
BRACKETED_PASTE_END = "\x1b[201~"


def is_paste_command(text: str) -> bool:
    """Return True when text is one of the paste-mode commands."""
    value = (text or "").strip().lower()
    return value in {"/paste", "paste", ":paste"}


def collect_paste_message(read_line, prompt: str, end_markers: set[str] | None = None) -> str:
    """Collect multiline paste input until an end marker line is entered."""
    markers = end_markers if end_markers is not None else PASTE_END_MARKERS
    lines: list[str] = []
    while True:
        line = read_line(prompt)
        if line.strip().lower() in markers:
            break
        lines.append(line)
    return "\n".join(lines)


def is_bracketed_paste_start(text: str, start_marker: str = BRACKETED_PASTE_START) -> bool:
    """Return True when line appears to begin a bracketed paste payload."""
    return start_marker in (text or "")


def collect_bracketed_paste(
    first_line: str,
    read_line,
    prompt: str,
    start_marker: str = BRACKETED_PASTE_START,
    end_marker: str = BRACKETED_PASTE_END,
) -> str:
    """Collect a terminal bracketed-paste payload into one message."""
    line = (first_line or "")
    if start_marker in line:
        line = line.replace(start_marker, "", 1)

    lines: list[str] = []
    while True:
        if end_marker in line:
            before, _sep, _after = line.partition(end_marker)
            lines.append(before)
            break
        lines.append(line)
        line = read_line(prompt)
    return "\n".join(lines)
