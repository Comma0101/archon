"""Terminal UI helpers for Archon CLI."""

from __future__ import annotations

import sys
import threading


ANSI_RESET = "\033[0m"
ANSI_PROMPT_USER = "\033[93;1m"      # bright yellow + bold
ANSI_PROMPT_ARCHON = "\033[92;1m"    # bright green + bold
ANSI_SPINNER = "\033[94m"            # bright blue
ANSI_ERROR = "\033[91;1m"            # bright red + bold
ANSI_PATH = "\033[96m"               # bright cyan
ANSI_DIM = "\033[2m"                 # dim
READLINE_IGNORE_START = "\001"
READLINE_IGNORE_END = "\002"


def _make_readline_prompt(label: str, color_ansi: str) -> str:
    """Build a readline-safe prompt with ANSI styling.

    readline needs non-printing sequences wrapped in \\001/\\002 so long input
    editing and line wrapping keep the correct cursor position.
    """
    return (
        f"{READLINE_IGNORE_START}{color_ansi}{READLINE_IGNORE_END}"
        f"{label}"
        f"{READLINE_IGNORE_START}{ANSI_RESET}{READLINE_IGNORE_END} "
    )


class _Spinner:
    """Terminal thinking indicator that runs in a background thread."""

    FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self):
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._label = "thinking"

    def start(self, label: str = "thinking"):
        self.stop()
        self._label = label
        self._stop.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def stop(self):
        if self._thread and self._thread.is_alive():
            self._stop.set()
            self._thread.join(timeout=1)
        # Clear the spinner line
        sys.stderr.write("\r\033[K")
        sys.stderr.flush()

    def _spin(self):
        i = 0
        while not self._stop.is_set():
            frame = self.FRAMES[i % len(self.FRAMES)]
            sys.stderr.write(f"\r{ANSI_SPINNER}{frame} {self._label}...{ANSI_RESET}")
            sys.stderr.flush()
            i += 1
            self._stop.wait(0.08)


def _format_chat_response(text: str) -> str:
    """Format assistant output for terminal readability (especially multiline)."""
    body = text or "(empty response)"
    lines = body.splitlines() or [body]
    if len(lines) == 1:
        return f"\n{ANSI_PROMPT_ARCHON}archon>{ANSI_RESET} {lines[0]}\n"
    indent = " " * 8
    rendered = [f"\n{ANSI_PROMPT_ARCHON}archon>{ANSI_RESET} {lines[0]}"]
    rendered.extend(f"{indent}{line}" for line in lines[1:])
    rendered.append("")
    return "\n".join(rendered)


def _format_turn_stats(
    elapsed: float,
    turn_in: int,
    turn_out: int,
    total_in: int,
    total_out: int,
    *,
    phase_label: str = "",
    route_lane: str = "",
    route_reason: str = "",
) -> str:
    """Format a dim per-turn stats line."""
    total = total_in + total_out
    phase = str(phase_label or "").strip()
    lane = (route_lane or "").strip().lower()
    reason = str(route_reason or "").strip().replace("_", " ")
    phase_suffix = f" | phase: {phase}" if phase else ""
    route_suffix = ""
    if lane:
        route_suffix = f" | route: {lane}"
        if reason:
            route_suffix += f" ({reason})"
    return (
        f"{ANSI_DIM}  {elapsed:.1f}s | {turn_in:,} in | {turn_out:,} out | "
        f"session: {total:,} tokens{phase_suffix}{route_suffix}{ANSI_RESET}"
    )


def _format_session_summary(
    turn_count: int,
    total_in: int,
    total_out: int,
    *,
    route_counts: dict[str, int] | None = None,
) -> str:
    """Format the session exit summary line."""
    total = total_in + total_out
    route_summary = ""
    if route_counts:
        ordered = []
        for lane in ("fast", "operator", "job"):
            count = int(route_counts.get(lane, 0) or 0)
            if count > 0:
                ordered.append(f"{lane}={count}")
        for lane, count in sorted(route_counts.items()):
            if lane in {"fast", "operator", "job"}:
                continue
            value = int(count or 0)
            if value > 0:
                ordered.append(f"{lane}={value}")
        if ordered:
            route_summary = f" | routes: {', '.join(ordered)}"
    return (
        f"{ANSI_DIM}Session: {turn_count} turns | {total_in:,} in | {total_out:,} out | "
        f"{total:,} total tokens{route_summary}{ANSI_RESET}"
    )
