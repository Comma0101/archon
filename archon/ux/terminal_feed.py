"""Readline-safe terminal activity feed primitives."""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable

from archon.security.redaction import sanitize_terminal_notice_text, strip_readline_prompt_markers
from archon.ux.events import ActivityEvent, UXEvent


class TerminalActivityFeed:
    """Render compact notices above the current prompt and redraw typed input."""

    def __init__(
        self,
        *,
        prompt_fn: Callable[[], str] | None = None,
        input_fn: Callable[[], str] | None = None,
        write_fn: Callable[[str], object] | None = None,
        flush_fn: Callable[[], object] | None = None,
        lock: threading.Lock | None = None,
    ) -> None:
        self._prompt_fn = prompt_fn or (lambda: "")
        self._input_fn = input_fn or (lambda: "")
        self._write_fn = write_fn or sys.stderr.write
        self._flush_fn = flush_fn or sys.stderr.flush
        self._lock = lock or threading.Lock()

    @property
    def current_prompt(self) -> str:
        return strip_readline_prompt_markers(self._safe_text(self._prompt_fn))

    def emit(self, event: ActivityEvent) -> None:
        self.emit_text(event.render_text())

    def emit_ux_event(self, event: UXEvent) -> None:
        self.emit_text(event.render_text())

    def emit_text(self, text: str) -> None:
        with self._lock:
            self._write_fn("\r\033[K")
            self._write_fn(sanitize_terminal_notice_text(text))
            # Use CRLF so the prompt redraw always starts in column 0 even when the
            # terminal does not translate bare LF while readline is active.
            self._write_fn("\r\n")
            prompt = self.current_prompt
            buffer_text = strip_readline_prompt_markers(self._safe_text(self._input_fn))
            if prompt or buffer_text:
                self._write_fn(f"{prompt}{buffer_text}")
            self._flush_fn()

    def _safe_text(self, fn: Callable[[], str]) -> str:
        try:
            return str(fn() or "")
        except Exception:
            return ""
