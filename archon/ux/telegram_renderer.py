"""Telegram surface renderer for tool feedback."""

from __future__ import annotations

import threading
from collections.abc import Callable

from archon.ux.events import UXEvent
from archon.ux.renderers import collapse_output_lines, truncate_diff_lines


class OutputBatchCollector:
    """Collect tool output lines and flush them as one Telegram code block."""

    def __init__(
        self,
        *,
        flush_fn: Callable[[str], None],
        interval_s: float = 3.0,
    ) -> None:
        self._flush_fn = flush_fn
        self._interval_s = interval_s
        self._lines: list[str] = []
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None

    def add_line(self, line: str) -> None:
        with self._lock:
            self._lines.append(str(line or ""))
            if self._timer is None:
                self._timer = threading.Timer(self._interval_s, self._timed_flush)
                self._timer.daemon = True
                self._timer.start()

    def flush(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            lines = self._lines[:]
            self._lines.clear()
        self._emit_lines(lines)

    def cancel(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._lines.clear()

    def _timed_flush(self) -> None:
        with self._lock:
            self._timer = None
            lines = self._lines[:]
            self._lines.clear()
        self._emit_lines(lines)

    def _emit_lines(self, lines: list[str]) -> None:
        if not lines:
            return
        collapsed = collapse_output_lines(lines)
        body = "\n".join(collapsed)
        self._flush_fn(f"```\n{body}\n```")


class TelegramRenderer:
    """Format UX events into Telegram-friendly plain text."""

    def format_event(self, event: UXEvent, *, status: str = "") -> str:
        kind = event.kind
        data = event.data
        if kind == "tool_end":
            summary = data.get("result", "") or f"{data.get('name', '?')}: done"
            if status == "failed":
                return f"✗ {summary}"
            return f"✓ {summary}"
        if kind == "tool_blocked":
            preview = data.get("command_preview", "?")
            level = data.get("safety_level", "DANGEROUS")
            return f"⚠️ Blocked: `{preview}` ({level})"
        if kind == "tool_running":
            if data.get("detail_type") == "output_line":
                return str(data.get("line", "") or "")
            if data.get("detail_type") == "heartbeat":
                tool = data.get("tool", "?")
                elapsed = float(data.get("elapsed_s", 0) or 0)
                return f"⏳ {tool} ({elapsed:.0f}s)"
        if kind == "tool_diff":
            diff_lines = truncate_diff_lines(list(data.get("diff_lines") or []))
            body = "\n".join(diff_lines)
            return f"```diff\n{body}\n```"
        return event.render_text()
