"""Tests for native subagent type definitions."""

from archon.subagents import GENERAL_SUBAGENT_TYPE, EXPLORE_SUBAGENT_TYPE, get_subagent_type


def test_subagent_types_exist_and_map_to_expected_tiers():
    explore = get_subagent_type("explore")
    general = get_subagent_type("general")

    assert explore is EXPLORE_SUBAGENT_TYPE
    assert general is GENERAL_SUBAGENT_TYPE
    assert explore.tier == "light"
    assert general.tier == "standard"


def test_explore_allowlist_contains_only_read_oriented_local_tools():
    explore = get_subagent_type("explore")

    assert set(explore.allowed_tools) == {"read_file", "grep", "glob", "list_dir", "shell"}


def test_general_excludes_spawn_worker_and_call_tools():
    general = get_subagent_type("general")

    assert "spawn_subagent" not in general.allowed_tools
    assert "delegate_code_task" not in general.allowed_tools
    assert not any(name.startswith("worker_") for name in general.allowed_tools)
    assert "call_mission_start" not in general.allowed_tools
    assert "call_mission_status" not in general.allowed_tools
    assert "call_mission_list" not in general.allowed_tools
    assert "call_mission_cancel" not in general.allowed_tools
    assert "deep_research" not in general.allowed_tools
