"""CLI surface renderer for tool feedback."""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable

from archon.ux.events import UXEvent
from archon.ux.renderers import truncate_diff_lines

ANSI_RESET = "\033[0m"
ANSI_DIM = "\033[2m"
ANSI_RED = "\033[91m"
ANSI_GREEN = "\033[92m"
ANSI_YELLOW = "\033[93m"


class CLIRenderer:
    """Render structured UX events to stderr without breaking prompt redraw."""

    def __init__(
        self,
        *,
        write_fn: Callable[[str], object] | None = None,
        flush_fn: Callable[[], object] | None = None,
        lock: threading.Lock | None = None,
    ) -> None:
        self._write = write_fn or sys.stderr.write
        self._flush = flush_fn or sys.stderr.flush
        self._lock = lock or threading.Lock()

    def render_event(self, event: UXEvent, *, status: str = "") -> None:
        kind = event.kind
        data = event.data
        if kind == "tool_end":
            summary = data.get("result", "") or f"{data.get('name', '?')}: done"
            if status == "failed":
                self._emit(f"{ANSI_RED}✗ {summary}{ANSI_RESET}")
            else:
                self._emit(f"{ANSI_DIM}✓ {summary}{ANSI_RESET}")
            return
        if kind == "tool_blocked":
            preview = data.get("command_preview", "?")
            level = data.get("safety_level", "DANGEROUS")
            self._emit(
                f"{ANSI_YELLOW}⚠ blocked: {preview} ({level}) - /approve or /deny{ANSI_RESET}"
            )
            return
        if kind == "tool_running":
            if data.get("detail_type") == "output_line":
                self._emit(f"{ANSI_DIM}│ {data.get('line', '')}{ANSI_RESET}")
                return
            if data.get("detail_type") == "heartbeat":
                tool = data.get("tool", "?")
                elapsed = float(data.get("elapsed_s", 0) or 0)
                self._emit(f"{ANSI_DIM}⠹ {tool} ({elapsed:.0f}s){ANSI_RESET}")
                return
        if kind == "tool_diff":
            diff_lines = list(data.get("diff_lines") or [])
            if not diff_lines:
                diff_text = str(data.get("diff_text", "") or "")
                diff_lines = diff_text.splitlines()
            for line in truncate_diff_lines(diff_lines):
                if line.startswith("-"):
                    rendered = f"  {ANSI_RED}{line}{ANSI_RESET}"
                elif line.startswith("+"):
                    rendered = f"  {ANSI_GREEN}{line}{ANSI_RESET}"
                else:
                    rendered = f"  {ANSI_DIM}{line}{ANSI_RESET}"
                self._emit(rendered)

    def _emit(self, text: str) -> None:
        with self._lock:
            self._write(f"\r\033[K{text}\n")
            self._flush()
