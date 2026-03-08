"""Tests for the live slash palette helpers."""

from archon.cli_commands import build_slash_commands, build_slash_subvalues, MODEL_CATALOG
from archon.config import Config, MCPServerConfig, ProfileConfig
from archon.slash_palette import build_palette_items, filter_palette_items


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
