"""Tests for the live slash palette helpers."""

import io
import os

from archon.cli_commands import build_slash_commands, build_slash_subvalues, MODEL_CATALOG
from archon.config import Config, MCPServerConfig, ProfileConfig
from archon.slash_palette import (
    build_palette_items,
    filter_palette_items,
    read_interactive_input,
    run_live_slash_palette,
)


def _config() -> Config:
    cfg = Config()
    cfg.profiles = {
        "default": ProfileConfig(),
        "safe": ProfileConfig(allowed_tools=["read_file"]),
    }
    cfg.mcp.servers = {
        "exa": MCPServerConfig(enabled=True, mode="read_only", transport="stdio"),
    }
    return cfg


def test_build_palette_items_expands_executable_leaf_commands():
    items = build_palette_items(
        build_slash_commands(),
        build_slash_subvalues(MODEL_CATALOG, _config()),
    )
    values = [value for value, _desc in items]

    assert "/status" in values
    assert "/profile set safe" in values
    assert "/mcp servers" in values
    assert "/mcp show exa" in values
    assert "/plugins show mcp:exa" in values
    assert "/mcp show" not in values
    assert "/plugins show" not in values


def test_build_palette_items_omits_hidden_alias_subcommands():
    items = build_palette_items(
        [("/jobs", "recent jobs")],
        {
            "/jobs": [("show research:abc", "Show one recent job")],
            "/job": [("research:abc", "Legacy alias for one recent job")],
        },
    )
    values = [value for value, _desc in items]

    assert "/jobs" in values
    assert "/jobs show research:abc" in values
    assert "/job research:abc" not in values


def test_filter_palette_items_shows_all_commands_for_root_query():
    items = build_palette_items(
        build_slash_commands(),
        build_slash_subvalues(MODEL_CATALOG, _config()),
    )

    matches = filter_palette_items(items, "/")
    values = [value for value, _desc in matches]

    assert "/status" in values
    assert "/model" in values
    assert "/profile" in values


def test_filter_palette_items_matches_nested_subcommands_token_by_token():
    items = build_palette_items(
        build_slash_commands(),
        build_slash_subvalues(MODEL_CATALOG, _config()),
    )

    matches = filter_palette_items(items, "/profile s")
    values = [value for value, _desc in matches]

    assert "/profile show" in values
    assert "/profile set safe" in values
    assert all(value.startswith("/profile ") for value in values)


class _FakeInputStream:
    def fileno(self):
        return 0

    def isatty(self):
        return True


def test_run_live_slash_palette_drills_into_top_level_command_before_executing(monkeypatch):
    items = build_palette_items(
        build_slash_commands(),
        build_slash_subvalues(MODEL_CATALOG, _config()),
    )
    reads = iter([b"\r", b"\r"])

    monkeypatch.setattr("archon.slash_palette.termios.tcgetattr", lambda _fd: [0])
    monkeypatch.setattr("archon.slash_palette.termios.tcsetattr", lambda *_args: None)
    monkeypatch.setattr("archon.slash_palette.tty.setraw", lambda _fd: None)
    monkeypatch.setattr("archon.slash_palette.os.get_terminal_size", lambda _fd: os.terminal_size((120, 40)))
    monkeypatch.setattr("archon.slash_palette.os.read", lambda _fd, _n: next(reads))

    result = run_live_slash_palette(
        prompt="you> ",
        items=items,
        input_stream=_FakeInputStream(),
        output_stream=io.StringIO(),
        initial_query="/profile",
    )

    assert result == "/profile show"


def test_run_live_slash_palette_drills_from_root_selection_into_subcommands(monkeypatch):
    items = build_palette_items(
        build_slash_commands(),
        build_slash_subvalues(MODEL_CATALOG, _config()),
    )
    reads = iter(
        [
            b"\x1b", b"[", b"B",
            b"\x1b", b"[", b"B",
            b"\x1b", b"[", b"B",
            b"\x1b", b"[", b"B",
            b"\x1b", b"[", b"B",
            b"\x1b", b"[", b"B",
            b"\x1b", b"[", b"B",
            b"\x1b", b"[", b"B",
            b"\r",
            b"\r",
        ]
    )

    monkeypatch.setattr("archon.slash_palette.termios.tcgetattr", lambda _fd: [0])
    monkeypatch.setattr("archon.slash_palette.termios.tcsetattr", lambda *_args: None)
    monkeypatch.setattr("archon.slash_palette.tty.setraw", lambda _fd: None)
    monkeypatch.setattr("archon.slash_palette.os.get_terminal_size", lambda _fd: os.terminal_size((120, 40)))
    monkeypatch.setattr("archon.slash_palette.os.read", lambda _fd, _n: next(reads))
    monkeypatch.setattr("archon.slash_palette.select.select", lambda *_args, **_kwargs: ([0], [], []))

    result = run_live_slash_palette(
        prompt="you> ",
        items=items,
        input_stream=_FakeInputStream(),
        output_stream=io.StringIO(),
        initial_query="/",
    )

    assert result == "/permissions status"


class _FakeReadline:
    def __init__(self):
        self._startup_hook = None
        self.inserted = []
        self.redisplay_calls = 0

    def set_startup_hook(self, hook):
        self._startup_hook = hook

    def insert_text(self, text):
        self.inserted.append(text)

    def redisplay(self):
        self.redisplay_calls += 1
        return None


def test_read_interactive_input_tracks_and_clears_transient_first_character(monkeypatch):
    events = []
    readline = _FakeReadline()

    monkeypatch.setattr("archon.slash_palette.termios.tcgetattr", lambda _fd: [0])
    monkeypatch.setattr("archon.slash_palette.termios.tcsetattr", lambda *_args: None)
    monkeypatch.setattr("archon.slash_palette.tty.setraw", lambda _fd: None)
    monkeypatch.setattr("archon.slash_palette.os.read", lambda _fd, _n: b"h")

    def _fallback_read(_prompt):
        assert callable(readline._startup_hook)
        readline._startup_hook()
        return "hello"

    result, used_palette = read_interactive_input(
        prompt="you> ",
        fallback_read_fn=_fallback_read,
        readline_module=readline,
        slash_commands=build_slash_commands(),
        slash_subvalues=build_slash_subvalues(MODEL_CATALOG, _config()),
        input_stream=_FakeInputStream(),
        output_stream=io.StringIO(),
        set_visible_input_fn=events.append,
    )

    assert (result, used_palette) == ("hello", False)
    assert events == ["h", None]


def test_read_interactive_input_does_not_force_redisplay_for_first_character(monkeypatch):
    readline = _FakeReadline()

    monkeypatch.setattr("archon.slash_palette.termios.tcgetattr", lambda _fd: [0])
    monkeypatch.setattr("archon.slash_palette.termios.tcsetattr", lambda *_args: None)
    monkeypatch.setattr("archon.slash_palette.tty.setraw", lambda _fd: None)
    monkeypatch.setattr("archon.slash_palette.os.read", lambda _fd, _n: b"h")

    def _fallback_read(_prompt):
        assert callable(readline._startup_hook)
        readline._startup_hook()
        return "hello"

    result, used_palette = read_interactive_input(
        prompt="you> ",
        fallback_read_fn=_fallback_read,
        readline_module=readline,
        slash_commands=build_slash_commands(),
        slash_subvalues=build_slash_subvalues(MODEL_CATALOG, _config()),
        input_stream=_FakeInputStream(),
        output_stream=io.StringIO(),
    )

    assert (result, used_palette) == ("hello", False)
    assert readline.inserted == ["h"]
    assert readline.redisplay_calls == 0


def test_read_interactive_input_restores_prompt_ownership_to_readline(monkeypatch):
    readline = _FakeReadline()
    output = io.StringIO()
    fallback_prompts = []

    monkeypatch.setattr("archon.slash_palette.termios.tcgetattr", lambda _fd: [0])
    monkeypatch.setattr("archon.slash_palette.termios.tcsetattr", lambda *_args: None)
    monkeypatch.setattr("archon.slash_palette.tty.setraw", lambda _fd: None)
    monkeypatch.setattr("archon.slash_palette.os.read", lambda _fd, _n: b"h")

    def _fallback_read(prompt):
        fallback_prompts.append(prompt)
        assert callable(readline._startup_hook)
        readline._startup_hook()
        return "hello"

    result, used_palette = read_interactive_input(
        prompt="\x01\033[93;1m\x02you>\x01\033[0m\x02 ",
        fallback_read_fn=_fallback_read,
        readline_module=readline,
        slash_commands=build_slash_commands(),
        slash_subvalues=build_slash_subvalues(MODEL_CATALOG, _config()),
        input_stream=_FakeInputStream(),
        output_stream=output,
    )

    assert (result, used_palette) == ("hello", False)
    assert fallback_prompts == ["\x01\033[93;1m\x02you>\x01\033[0m\x02 "]
    assert readline.inserted == ["h"]


def test_run_live_slash_palette_clears_visible_query_when_backspacing_from_root(monkeypatch):
    items = build_palette_items(
        build_slash_commands(),
        build_slash_subvalues(MODEL_CATALOG, _config()),
    )
    reads = iter([b"\x7f"])
    visible_queries = []

    monkeypatch.setattr("archon.slash_palette.termios.tcgetattr", lambda _fd: [0])
    monkeypatch.setattr("archon.slash_palette.termios.tcsetattr", lambda *_args: None)
    monkeypatch.setattr("archon.slash_palette.tty.setraw", lambda _fd: None)
    monkeypatch.setattr("archon.slash_palette.os.get_terminal_size", lambda _fd: os.terminal_size((120, 40)))
    monkeypatch.setattr("archon.slash_palette.os.read", lambda _fd, _n: next(reads))

    result = run_live_slash_palette(
        prompt="you> ",
        items=items,
        input_stream=_FakeInputStream(),
        output_stream=io.StringIO(),
        initial_query="/",
        set_visible_input_fn=visible_queries.append,
    )

    assert result == ""
    assert visible_queries == ["/", None]
