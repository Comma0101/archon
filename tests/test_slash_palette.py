"""Tests for the live slash palette helpers."""

import io
import os

from archon.cli_commands import build_slash_commands, build_slash_subvalues, MODEL_CATALOG
from archon.config import Config, MCPServerConfig, ProfileConfig
from archon.slash_palette import (
    build_palette_items,
    filter_palette_items,
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
