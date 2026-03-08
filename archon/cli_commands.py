"""Slash-command and interactive picker helpers for Archon CLI."""

from __future__ import annotations

import os
import select
import sys
import termios
import tty

from archon.calls.store import list_call_job_summaries
from archon.control.skills import list_builtin_skills
from archon.research.store import list_research_job_summaries
from archon.workers.session_store import list_worker_job_summaries


SLASH_COMMAND_GROUPS = (
    (
        "Shell",
        (
            ("/help", "commands and usage"),
            ("/reset", "clear conversation"),
            ("/status", "current status"),
            ("/cost", "token usage"),
            ("/clear", "clear history"),
            ("/compact", "compact context"),
            ("/context", "context state"),
            ("/doctor", "health checks"),
            ("/permissions", "policy permissions"),
            ("/approvals", "approvals status/on/off"),
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
            ("/model", "current or set provider/model"),
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
    for provider in sorted(model_catalog):
        for model in sorted(model_catalog[provider]):
            items.append((f"{provider}-{model}", provider))
    return items


def _runtime_mcp_server_names(config) -> list[str]:
    mcp = getattr(config, "mcp", None)
    servers = getattr(mcp, "servers", None)
    if not isinstance(servers, dict):
        return []
    names = {
        str(name).strip().lower()
        for name in servers.keys()
        if str(name).strip()
    }
    return sorted(names)


def _runtime_profile_names(config) -> list[str]:
    profiles = getattr(config, "profiles", None)
    if isinstance(profiles, dict) and profiles:
        names = {str(name).strip() for name in profiles if str(name).strip()}
        if names:
            return sorted(names)
    return ["default"]


def _builtin_skill_names() -> list[str]:
    return sorted(skill.name for skill in list_builtin_skills())


def _recent_job_refs(limit: int = 8) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    try:
        jobs = []
        jobs.extend(list_worker_job_summaries(limit=limit))
        jobs.extend(list_call_job_summaries(limit=limit))
        jobs.extend(list_research_job_summaries(limit=limit))
        jobs.sort(
            key=lambda item: str(getattr(item, "last_update_at", "") or ""),
            reverse=True,
        )
        for job in jobs:
            job_id = str(getattr(job, "job_id", "") or "").strip()
            if not job_id or job_id in seen:
                continue
            seen.add(job_id)
            refs.append(job_id)
            if len(refs) >= limit:
                break
    except OSError:
        return []
    return refs


def _native_plugin_names() -> list[str]:
    return ["calls", "telegram", "web"]


def build_slash_subvalues(
    model_catalog_or_config,
    runtime_config=None,
) -> dict[str, list[tuple[str, str]]]:
    """Build slash command subvalue map."""
    model_catalog = (
        model_catalog_or_config
        if isinstance(model_catalog_or_config, dict)
        else MODEL_CATALOG
    )
    config = runtime_config if runtime_config is not None else (
        None if isinstance(model_catalog_or_config, dict) else model_catalog_or_config
    )
    mcp_servers = _runtime_mcp_server_names(config)
    plugin_values = [
        ("list", "List native and MCP plugins"),
        ("show", "Show one plugin"),
    ]
    plugin_values.extend(
        (f"show {name}", "Show one native plugin")
        for name in _native_plugin_names()
    )
    plugin_values.extend(
        (f"show mcp:{server}", "Show one MCP plugin")
        for server in mcp_servers
    )
    profile_names = _runtime_profile_names(config)
    skill_names = _builtin_skill_names()
    job_refs = _recent_job_refs()
    mcp_values: list[tuple[str, str]] = [
        ("servers", "List configured MCP servers"),
        ("show", "Show one MCP server config"),
        ("tools", "List advertised tools for one server"),
    ]
    for server in mcp_servers:
        mcp_values.append((f"show {server}", "Show one MCP server config"))
        mcp_values.append((f"tools {server}", "List advertised tools for one server"))
    return {
        "/model": [
            ("set", "Set provider-model"),
            *( (f"set {value}", provider) for value, provider in build_model_set_subvalues(model_catalog) ),
        ],
        "/approvals": [
            ("status", "Show current terminal approval status"),
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
            *( (f"set {name}", f"Set session policy profile to {name}") for name in profile_names ),
        ],
        "/skills": [
            ("list", "List available built-in skills"),
            *( (f"show {name}", "Show one skill profile") for name in skill_names ),
            *( (f"use {name}", "Set session skill") for name in skill_names ),
            ("clear", "Clear active session skill"),
        ],
        "/plugins": [
            *plugin_values,
        ],
        "/permissions": [
            ("auto", "Set permission mode to auto"),
            ("accept_reads", "Set permission mode to accept_reads"),
            ("confirm_all", "Set permission mode to confirm_all"),
        ],
        "/mcp": [
            *mcp_values,
        ],
        "/jobs": [
            ("active", "Show unresolved jobs"),
            ("all", "Show recent jobs"),
            ("purge", "Purge local terminal job records"),
        ],
        "/job": [
            *[(job_id, "Show one recent job") for job_id in job_refs],
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


def _picker_leaf_subvalues(values: list[tuple[str, str]]) -> list[tuple[str, str]]:
    raw_values = [str(value or "").strip() for value, _desc in values]
    leaf_items: list[tuple[str, str]] = []
    for value, desc in values:
        normalized = str(value or "").strip()
        if not normalized:
            continue
        prefix = normalized + " "
        if any(other.startswith(prefix) for other in raw_values if other != normalized):
            continue
        leaf_items.append((normalized, desc))
    return leaf_items


def _picker_selectable_subvalues(
    command: str,
    values: list[tuple[str, str]],
    *,
    typed_remainder: str = "",
) -> list[tuple[str, str]]:
    picker_values = _picker_leaf_subvalues(values)
    remainder = str(typed_remainder or "")
    if remainder.strip():
        picker_values = [
            (value, desc)
            for value, desc in picker_values
            if _subvalue_matches_remainder(value, remainder)
        ]
    blocked_by_command = {
        "/plugins": {"show"},
        "/mcp": {"show", "tools"},
    }
    blocked = blocked_by_command.get(str(command or "").strip(), set())
    if not blocked:
        return picker_values
    return [(value, desc) for value, desc in picker_values if value not in blocked]


def _subvalue_matches_remainder(value: str, typed_remainder: str) -> bool:
    remainder = str(typed_remainder or "")
    ends_with_space = remainder.endswith(" ")
    stripped = remainder.strip()
    if not stripped:
        return True
    typed_tokens = stripped.split()
    value_tokens = str(value or "").strip().split()
    if not value_tokens:
        return False
    prefix_tokens = typed_tokens if ends_with_space else typed_tokens[:-1]
    if len(value_tokens) < len(prefix_tokens):
        return False
    if value_tokens[: len(prefix_tokens)] != prefix_tokens:
        return False
    if ends_with_space:
        return len(value_tokens) > len(prefix_tokens)
    current_prefix = typed_tokens[-1]
    token_index = len(prefix_tokens)
    if len(value_tokens) <= token_index:
        return False
    return value_tokens[token_index].startswith(current_prefix)


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
    query: str | None = None,
) -> str | None:
    """Interactive two-level command picker with optional sub-value selection."""
    query_text = str(query or "").strip()
    command = None
    typed_remainder = ""
    command_from_partial_query = False

    if not query_text or query_text == "/":
        command = run_picker_fn(list(slash_commands), label_width=12)
        if command is None:
            return None
    elif not query_text.startswith("/"):
        return None
    elif " " not in query_text:
        command_matches = [
            (name, desc) for name, desc in slash_commands if name.startswith(query_text)
        ]
        if not command_matches:
            return None
        exact_command = next((name for name, _desc in command_matches if name == query_text), None)
        if exact_command is not None:
            command = exact_command
        else:
            command = run_picker_fn(command_matches, label_width=12)
            if command is None:
                return None
            command_from_partial_query = True
    else:
        command_token, typed_remainder = query_text.split(" ", 1)
        command_matches = [
            (name, desc) for name, desc in slash_commands if name.startswith(command_token)
        ]
        if not command_matches:
            return None
        exact_command = next((name for name, _desc in command_matches if name == command_token), None)
        if exact_command is not None:
            command = exact_command
        else:
            command = run_picker_fn(command_matches, label_width=12)
            if command is None:
                return None

    if command_from_partial_query and not typed_remainder:
        return command

    subvalues = slash_subvalues.get(command)
    if not subvalues:
        return command
    picker_values = _picker_selectable_subvalues(
        command,
        subvalues,
        typed_remainder=typed_remainder,
    )
    if not picker_values:
        return command
    max_len = max(len(v) for v, _ in picker_values)
    value = run_picker_fn(picker_values, label_width=max_len + 2)
    if value is None:
        return None
    return f"{command} {value}"
