"""Telegram surface renderer for tool feedback."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from archon.adapters.telegram_client import DEFAULT_TELEGRAM_MESSAGE_LIMIT
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


class LiveReplyEditor:
    """Manage one editable Telegram reply for streamed assistant text."""

    def __init__(
        self,
        *,
        send_fn: Callable[[str], dict],
        edit_fn: Callable[[int, str], None],
        fallback_send_fn: Callable[[str], None],
        time_fn: Callable[[], float] = time.monotonic,
        throttle_s: float = 0.75,
        min_start_chars: int = 24,
        start_timeout_s: float = 0.75,
        message_limit: int = DEFAULT_TELEGRAM_MESSAGE_LIMIT,
    ) -> None:
        self._send_fn = send_fn
        self._edit_fn = edit_fn
        self._fallback_send_fn = fallback_send_fn
        self._time_fn = time_fn
        self._throttle_s = max(0.0, float(throttle_s))
        self._min_start_chars = max(1, int(min_start_chars))
        self._start_timeout_s = max(0.0, float(start_timeout_s))
        self._message_limit = max(1, int(message_limit))
        self._message_id: int | None = None
        self._first_chunk_at: float | None = None
        self._last_sent_text = ""
        self._last_edit_at = 0.0
        self._fallback_mode = False

    def observe(self, text: str) -> None:
        current = str(text or "")
        if not current or self._fallback_mode or len(current) > self._message_limit:
            return
        now = self._time_fn()
        if self._first_chunk_at is None:
            self._first_chunk_at = now
        if self._message_id is None:
            if len(current) < self._min_start_chars and (now - self._first_chunk_at) < self._start_timeout_s:
                return
            try:
                result = self._send_fn(current)
            except Exception:
                self._fallback_mode = True
                return
            message_id = result.get("message_id")
            if not isinstance(message_id, int):
                self._fallback_mode = True
                return
            self._message_id = message_id
            self._last_sent_text = current
            self._last_edit_at = now
            return
        if current == self._last_sent_text or (now - self._last_edit_at) < self._throttle_s:
            return
        self._edit_or_fallback(current, now)

    def finalize(self, text: str) -> bool:
        final_text = str(text or "")
        if not final_text:
            return False
        if self._fallback_mode or len(final_text) > self._message_limit:
            if final_text != self._last_sent_text:
                self._fallback_send_fn(final_text)
                self._last_sent_text = final_text
            return True
        if self._message_id is None:
            return False
        if final_text == self._last_sent_text:
            return True
        self._edit_or_fallback(final_text, self._time_fn())
        return True

    def _edit_or_fallback(self, text: str, now: float) -> None:
        if self._message_id is None:
            self._fallback_mode = True
            if text != self._last_sent_text:
                self._fallback_send_fn(text)
                self._last_sent_text = text
            return
        try:
            self._edit_fn(self._message_id, text)
            self._last_sent_text = text
            self._last_edit_at = now
        except Exception:
            self._fallback_mode = True
            if text != self._last_sent_text:
                self._fallback_send_fn(text)
                self._last_sent_text = text


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
