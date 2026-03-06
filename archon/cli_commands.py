"""Slash-command and interactive picker helpers for Archon CLI."""

from __future__ import annotations

import os
import select
import sys
import termios
import tty


SLASH_COMMAND_GROUPS = (
    (
        "Shell",
        (
            ("/help", "commands and usage"),
            ("/reset", "clear conversation"),
            ("/status", "current status"),
            ("/cost", "token usage"),
            ("/compact", "compact context"),
            ("/context", "context state"),
            ("/doctor", "health checks"),
            ("/permissions", "policy permissions"),
            ("/approvals", "approval status"),
            ("/approve", "approve pending request"),
            ("/deny", "deny pending request"),
            ("/approve_next", "approve next dangerous action"),
            ("/skills", "skills"),
            ("/plugins", "plugins"),
        ),
    ),
    (
        "Model",
        (
            ("/model", "current provider/model"),
            ("/model-list", "list presets"),
            ("/model-set", "set provider-model"),
        ),
    ),
    (
        "Control",
        (
            ("/calls", "call controls"),
            ("/profile", "policy profiles"),
            ("/jobs", "recent jobs"),
            ("/job", "job summary"),
        ),
    ),
    (
        "Integrations",
        (
            ("/mcp", "MCP servers and tools"),
        ),
    ),
    (
        "Input",
        (
            ("/paste", "multiline paste"),
        ),
    ),
)


def build_slash_commands() -> list[tuple[str, str]]:
    commands: list[tuple[str, str]] = []
    for group, items in SLASH_COMMAND_GROUPS:
        for name, description in items:
            commands.append((name, f"{group}: {description}"))
    return commands


SLASH_COMMANDS = build_slash_commands()

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
        "/approvals": [
            ("on", "Enable sticky dangerous-action approvals"),
            ("off", "Disable sticky dangerous-action approvals"),
        ],
        "/calls": [
            ("status", "Show current calls config"),
            ("on", "Enable calls feature"),
            ("off", "Disable calls feature"),
        ],
        "/profile": [
            ("show", "Show active policy profile"),
            ("set default", "Set session policy profile to default"),
        ],
        "/skills": [
            ("list", "List available built-in skills"),
            ("show coder", "Show one skill profile"),
            ("use coder", "Set session skill"),
            ("clear", "Clear active session skill"),
        ],
        "/plugins": [
            ("list", "List native and MCP plugins"),
            ("show calls", "Show one native plugin"),
            ("show mcp:docs", "Show one MCP plugin"),
        ],
        "/mcp": [
            ("servers", "List configured MCP servers"),
            ("show docs", "Show one MCP server config"),
            ("tools docs", "List advertised tools for one server"),
        ],
        "/jobs": [
            ("active", "Show unresolved jobs"),
            ("all", "Show recent jobs"),
        ],
    }


def slash_completer(
    text: str,
    state: int,
    slash_names: list[str],
    slash_subvalues: dict[str, list[tuple[str, str]]] | None = None,
    line_buffer: str = "",
) -> str | None:
    """readline completer for slash commands and token-aware subcommands."""
    subvalues = slash_subvalues or {}
    buffer = (line_buffer or "").lstrip()

    if buffer.startswith("/"):
        command, sep, _remainder = buffer.partition(" ")
        if sep and command in subvalues:
            typed_remainder = buffer[len(command) + 1 :]
            matches = _subcommand_token_matches(subvalues.get(command, []), typed_remainder)
            return matches[state] if state < len(matches) else None

    if text.startswith("/"):
        matches = [cmd for cmd in slash_names if cmd.startswith(text)]
    elif not text:
        matches = list(slash_names)
    else:
        matches = []
    return matches[state] if state < len(matches) else None


def _subcommand_token_matches(values: list[tuple[str, str]], typed_remainder: str) -> list[str]:
    remainder = str(typed_remainder or "")
    ends_with_space = remainder.endswith(" ")
    stripped = remainder.strip()
    typed_tokens = stripped.split() if stripped else []
    prefix_tokens = typed_tokens if ends_with_space else typed_tokens[:-1]
    current_prefix = "" if ends_with_space else (typed_tokens[-1] if typed_tokens else "")

    matches: list[str] = []
    seen: set[str] = set()
    for raw_value, _desc in values:
        value = str(raw_value or "").strip()
        if not value:
            continue
        parts = value.split()
        if len(parts) <= len(prefix_tokens):
            continue
        if parts[: len(prefix_tokens)] != prefix_tokens:
            continue
        token = parts[len(prefix_tokens)]
        if current_prefix and not token.startswith(current_prefix):
            continue
        if token in seen:
            continue
        seen.add(token)
        matches.append(token)
    return matches


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
