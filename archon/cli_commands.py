"""Slash-command and interactive picker helpers for Archon CLI."""

from __future__ import annotations

import os
import select
import sys
import termios
import tty


SLASH_COMMANDS = [
    ("/help", "Show commands and usage"),
    ("/reset", "Clear conversation history"),
    ("/status", "Show current shell status"),
    ("/cost", "Show session token usage"),
    ("/doctor", "Run local shell health checks"),
    ("/permissions", "Show current policy permissions"),
    ("/model", "Show current provider/model"),
    ("/model-list", "List model presets by provider"),
    ("/model-set", "Set model via <provider>-<model>"),
    ("/calls", "Call tool controls (status/on/off)"),
    ("/profile", "Policy profile controls (show/set)"),
    ("/mcp", "Inspect configured MCP servers and tools"),
    ("/jobs", "List recent cross-surface jobs"),
    ("/job", "Show one job summary by ID"),
    ("/paste", "Multiline paste mode"),
]

MODEL_CATALOG: dict[str, tuple[str, ...]] = {
    "google": (
        "gemini-3.1-pro-preview",
        "gemini-3-pro-preview",
        "gemini-3-flash-preview",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
    ),
    "openai": (
        "gpt-5.2",
        "gpt-5.2-pro",
        "gpt-5.2-codex",
        "gpt-5-mini",
        "gpt-5-nano",
        "gpt-4.1",
    ),
    "anthropic": (
        "claude-opus-4-1-20250805",
        "claude-sonnet-4-20250514",
        "claude-3-7-sonnet-20250219",
    ),
}


def build_model_set_subvalues(model_catalog: dict[str, tuple[str, ...]]) -> list[tuple[str, str]]:
    """Build sub-values for /model-set from a model catalog."""
    items: list[tuple[str, str]] = []
    for provider, models in model_catalog.items():
        for model in models:
            items.append((f"{provider}-{model}", provider))
    return items


def build_slash_subvalues(model_catalog: dict[str, tuple[str, ...]]) -> dict[str, list[tuple[str, str]]]:
    """Build slash command subvalue map."""
    return {
        "/model-set": build_model_set_subvalues(model_catalog),
        "/calls": [
            ("status", "Show current calls config"),
            ("on", "Enable calls feature"),
            ("off", "Disable calls feature"),
        ],
        "/profile": [
            ("show", "Show active policy profile"),
            ("set default", "Set session policy profile to default"),
        ],
        "/mcp": [
            ("servers", "List configured MCP servers"),
            ("tools", "List advertised tools for one server"),
        ],
    }


def slash_completer(
    text: str,
    state: int,
    slash_names: list[str],
    slash_subvalues: dict[str, list[tuple[str, str]]] | None = None,
    line_buffer: str = "",
) -> str | None:
    """readline completer for slash commands and first-level subcommands."""
    subvalues = slash_subvalues or {}
    buffer = (line_buffer or "").lstrip()

    if buffer.startswith("/"):
        command, sep, _remainder = buffer.partition(" ")
        if sep and command in subvalues:
            typed = (text or "").strip()
            # Keep completion lightweight: first sub-token only (e.g. /profile set|show).
            candidates = _first_subcommand_tokens(subvalues.get(command, []))
            matches = [value for value in candidates if value.startswith(typed)]
            return matches[state] if state < len(matches) else None

    if text.startswith("/"):
        matches = [cmd for cmd in slash_names if cmd.startswith(text)]
    elif not text:
        matches = list(slash_names)
    else:
        matches = []
    return matches[state] if state < len(matches) else None


def _first_subcommand_tokens(values: list[tuple[str, str]]) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for raw_value, _desc in values:
        value = str(raw_value or "").strip()
        if not value:
            continue
        token = value.split()[0]
        if token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def run_picker(items: list[tuple[str, str]], label_width: int = 10) -> str | None:
    """Interactive arrow-key picker. Returns selected item name or None."""
    if not items:
        return None
    try:
        fd = sys.stdin.fileno()
    except (AttributeError, OSError, ValueError):
        return None
    if not os.isatty(fd):
        return None

    selected = 0
    count = len(items)
    old = termios.tcgetattr(fd)

    def _write(s: str) -> None:
        sys.stdout.write(s)
        sys.stdout.flush()

    def _render(first: bool = False) -> None:
        if not first:
            _write(f"\033[{count}A")
        for i, (name, desc) in enumerate(items):
            if i == selected:
                _write(f"\r\033[K \033[96;1m> {name:<{label_width}} {desc}\033[0m\n")
            else:
                _write(f"\r\033[K   {name:<{label_width}} {desc}\n")

    def _clear() -> None:
        _write(f"\033[{count}A")
        for _ in range(count):
            _write("\r\033[K\n")
        _write(f"\033[{count}A")

    try:
        _render(first=True)
        tty.setcbreak(fd)
        while True:
            ch = os.read(fd, 1)
            if ch == b"\x1b":
                if select.select([fd], [], [], 0.05)[0]:
                    ch2 = os.read(fd, 1)
                    if ch2 == b"[" and select.select([fd], [], [], 0.05)[0]:
                        ch3 = os.read(fd, 1)
                        if ch3 == b"A":
                            selected = (selected - 1) % count
                            _render()
                            continue
                        if ch3 == b"B":
                            selected = (selected + 1) % count
                            _render()
                            continue
                _clear()
                return None
            if ch in (b"\r", b"\n"):
                _clear()
                return items[selected][0]
            if ch == b"\x03":
                _clear()
                return None
    except (OSError, ValueError):
        return None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def pick_slash_command(
    *,
    run_picker_fn,
    slash_commands: list[tuple[str, str]],
    slash_subvalues: dict[str, list[tuple[str, str]]],
) -> str | None:
    """Interactive two-level command picker with optional sub-value selection."""
    command = run_picker_fn(list(slash_commands), label_width=12)
    if command is None:
        return None
    subvalues = slash_subvalues.get(command)
    if not subvalues:
        return command
    max_len = max(len(v) for v, _ in subvalues)
    value = run_picker_fn(subvalues, label_width=max_len + 2)
    if value is None:
        return None
    return f"{command} {value}"
