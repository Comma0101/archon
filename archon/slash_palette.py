"""Live slash-palette helpers for the Archon terminal shell."""

from __future__ import annotations

import os
import select
import sys
import termios
import tty

from archon.cli_commands import _picker_selectable_subvalues


MAX_VISIBLE_MATCHES = 8


def build_palette_items(
    slash_commands: list[tuple[str, str]],
    slash_subvalues: dict[str, list[tuple[str, str]]],
) -> list[tuple[str, str]]:
    """Build executable slash command items for the live palette."""
    items: list[tuple[str, str]] = []
    seen: set[str] = set()
    visible_commands = {
        str(value or "").strip()
        for value, _desc in slash_commands
        if str(value or "").strip()
    }

    for value, desc in slash_commands:
        normalized = str(value or "").strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            items.append((normalized, desc))

    for command, values in slash_subvalues.items():
        if str(command or "").strip() not in visible_commands:
            continue
        for value, desc in _picker_selectable_subvalues(command, values):
            full_value = f"{command} {value}".strip()
            if full_value and full_value not in seen:
                seen.add(full_value)
                items.append((full_value, desc))

    return items


def filter_palette_items(
    items: list[tuple[str, str]],
    query: str,
) -> list[tuple[str, str]]:
    """Filter executable slash items token-by-token."""
    value = str(query or "")
    if not value.startswith("/"):
        return []

    ends_with_space = value.endswith(" ")
    query_tokens = value.split()
    if not query_tokens:
        return [i for i in items if len(str(i[0]).split()) == 1]

    # If the user hasn't typed a space yet, only show top-level commands
    is_top_level = not ends_with_space and len(query_tokens) == 1

    matches: list[tuple[str, str]] = []
    for item, desc in items:
        item_tokens = str(item or "").split()
        if len(item_tokens) < len(query_tokens):
            continue
            
        if is_top_level and len(item_tokens) > 1:
            continue
            
        prefix_tokens = query_tokens if ends_with_space else query_tokens[:-1]
        if item_tokens[: len(prefix_tokens)] != prefix_tokens:
            continue
        if ends_with_space:
            if len(item_tokens) == len(prefix_tokens):
                continue
            matches.append((item, desc))
            continue
        current_prefix = query_tokens[-1]
        token_index = len(prefix_tokens)
        if token_index >= len(item_tokens):
            continue
        if item_tokens[token_index].startswith(current_prefix):
            matches.append((item, desc))
    return matches


def read_interactive_input(
    *,
    prompt: str,
    fallback_read_fn,
    readline_module,
    slash_commands: list[tuple[str, str]],
    slash_subvalues: dict[str, list[tuple[str, str]]],
    input_stream=None,
    output_stream=None,
    set_visible_input_fn=None,
) -> tuple[str, bool]:
    """Read one interactive line, entering live slash mode on an initial `/`."""
    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout
    prompt_text = _sanitize_prompt(prompt)

    try:
        fd = input_stream.fileno()
    except (AttributeError, OSError, ValueError):
        return fallback_read_fn(prompt), False
    is_tty_fn = getattr(input_stream, "isatty", None)
    is_tty = bool(is_tty_fn()) if callable(is_tty_fn) else os.isatty(fd)
    if not is_tty:
        return fallback_read_fn(prompt), False

    output_stream.write(prompt_text)
    output_stream.flush()

    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        first = os.read(fd, 1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

    if first in (b"\x03",):
        raise KeyboardInterrupt
    if first in (b"\x04",):
        raise EOFError
    if first in (b"\r", b"\n"):
        output_stream.write("\n")
        output_stream.flush()
        return "", False
    if first == b"/":
        items = build_palette_items(slash_commands, slash_subvalues)
        return (
            run_live_slash_palette(
                prompt=prompt_text,
                items=items,
                input_stream=input_stream,
                output_stream=output_stream,
                initial_query="/",
                prompt_rendered=True,
                set_visible_input_fn=set_visible_input_fn,
            ),
            True,
        )

    first_text = first.decode("utf-8", errors="ignore")
    hook_set = getattr(readline_module, "set_startup_hook", None)
    hook_clear = getattr(readline_module, "set_startup_hook", None)
    insert_text = getattr(readline_module, "insert_text", None)
    redisplay = getattr(readline_module, "redisplay", None)
    if callable(set_visible_input_fn):
        set_visible_input_fn(first_text)
    if callable(hook_set) and callable(insert_text):
        def _startup_hook() -> None:
            insert_text(first_text)
            if callable(redisplay):
                redisplay()
            if callable(set_visible_input_fn):
                set_visible_input_fn(None)
            try:
                if callable(hook_clear):
                    hook_clear(None)
            except Exception:
                return None

        hook_set(_startup_hook)
        return fallback_read_fn(""), False

    try:
        return first_text + fallback_read_fn(""), False
    finally:
        if callable(set_visible_input_fn):
            set_visible_input_fn(None)


def run_live_slash_palette(
    *,
    prompt: str,
    items: list[tuple[str, str]],
    input_stream=None,
    output_stream=None,
    initial_query: str = "/",
    prompt_rendered: bool = False,
    set_visible_input_fn=None,
) -> str:
    """Run the slash palette until a command or raw slash text is returned."""
    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout
    fd = input_stream.fileno()
    query = initial_query or "/"
    selected = 0
    rendered_lines = 0
    old = termios.tcgetattr(fd)
    visible_value: str | None = None

    def _publish_visible(value: str | None) -> None:
        nonlocal visible_value
        if visible_value == value:
            return
        visible_value = value
        if callable(set_visible_input_fn):
            set_visible_input_fn(value)

    def _clamp_selected(matches: list[tuple[str, str]]) -> None:
        nonlocal selected
        if not matches:
            selected = 0
            return
        selected = max(0, min(selected, len(matches) - 1))

    def _selection_has_children(value: str) -> bool:
        normalized = str(value or "").strip()
        if not normalized or " " in normalized:
            return False
        prefix = normalized + " "
        return any(item.startswith(prefix) for item, _desc in items)

    def _clear() -> None:
        nonlocal rendered_lines
        if rendered_lines <= 0:
            return
        output_stream.write("\r\033[2K")
        for _ in range(rendered_lines - 1):
            output_stream.write("\033[1A\r\033[2K")
        output_stream.flush()
        rendered_lines = 0

    def _render() -> list[tuple[str, str]]:
        nonlocal rendered_lines
        matches = filter_palette_items(items, query)
        
        if not matches:
            start_idx = 0
            visible = []
        else:
            start_idx = max(0, min(selected - (MAX_VISIBLE_MATCHES // 2), len(matches) - MAX_VISIBLE_MATCHES))
            visible = matches[start_idx : start_idx + MAX_VISIBLE_MATCHES]

        _clear()

        try:
            columns = os.get_terminal_size(fd).columns
        except OSError:
            columns = 80

        output_stream.write("\r\033[2K")
        output_stream.write(f"{prompt}{query}")
        if visible:
            for i, (value, desc) in enumerate(visible):
                actual_idx = start_idx + i
                marker = ">" if actual_idx == selected else " "
                
                # Prefix visible length: space (1) + marker (1) + space (1) + value (max 26) + space (1) = 30
                prefix_len = 30
                max_desc_len = columns - prefix_len
                
                display_desc = desc
                if max_desc_len > 0 and len(desc) > max_desc_len:
                    if max_desc_len > 1:
                        display_desc = desc[:max_desc_len - 1] + "…"
                    else:
                        display_desc = ""
                elif max_desc_len <= 0:
                    display_desc = ""

                line = f"\r\n {'\033[96;1m' if actual_idx == selected else ''}{marker} {value:<26} {display_desc}{'\033[0m' if actual_idx == selected else ''}"
                output_stream.write(line)
            rendered_lines = 1 + len(visible)
        else:
            output_stream.write("\r\n   (no matches)")
            rendered_lines = 2
        output_stream.flush()
        return matches

    try:
        tty.setraw(fd)
        if not prompt_rendered:
            output_stream.write(prompt)
            output_stream.flush()
        _publish_visible(query)
        matches = _render()
        while True:
            ch = os.read(fd, 1)
            if ch in (b"\r", b"\n", b"\t"):
                chosen = matches[selected][0] if matches else query
                if _selection_has_children(chosen) and " " not in str(query or "").strip():
                    query = f"{chosen} "
                    _publish_visible(query)
                    selected = 0
                    matches = _render()
                    _clamp_selected(matches)
                    continue
                _clear()
                _publish_visible(None)
                return chosen
            if ch in (b"\x03",):
                _clear()
                _publish_visible(None)
                raise KeyboardInterrupt
            if ch in (b"\x04",):
                _clear()
                _publish_visible(None)
                raise EOFError
            if ch in (b"\x7f", b"\b"):
                query = query[:-1]
                if not query:
                    _clear()
                    _publish_visible(None)
                    return ""
                _publish_visible(query)
                matches = _render()
                _clamp_selected(matches)
                continue
            if ch == b"\x1b":
                if select.select([fd], [], [], 0.05)[0]:
                    ch2 = os.read(fd, 1)
                    if ch2 == b"[" and select.select([fd], [], [], 0.05)[0]:
                        ch3 = os.read(fd, 1)
                        if ch3 == b"A":
                            if matches:
                                selected = (selected - 1) % len(matches)
                            matches = _render()
                            continue
                        if ch3 == b"B":
                            if matches:
                                selected = (selected + 1) % len(matches)
                            matches = _render()
                            continue
                _clear()
                _publish_visible(None)
                return query
            try:
                decoded = ch.decode("utf-8", errors="ignore")
            except Exception:
                decoded = ""
            if decoded and decoded.isprintable():
                query += decoded
                _publish_visible(query)
                matches = _render()
                _clamp_selected(matches)
                continue
    finally:
        _publish_visible(None)
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _sanitize_prompt(prompt: str) -> str:
    return str(prompt or "").replace("\x01", "").replace("\x02", "")
