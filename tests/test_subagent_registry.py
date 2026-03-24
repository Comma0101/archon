"""Tests for ToolRegistry.empty()."""

from archon.config import Config
from archon.safety import Level
from archon.subagents.registry import build_subagent_registry
from archon.subagents.types import get_subagent_type
from archon.tools import ToolRegistry


def test_tool_registry_empty_initializes_core_fields_without_builtins():
    confirmer = lambda *_args, **_kwargs: True
    config = Config()

    registry = ToolRegistry.empty(
        archon_source_dir="/tmp/archon-src",
        confirmer=confirmer,
        config=config,
    )

    assert registry.tools == {}
    assert registry.handlers == {}
    assert registry.archon_source_dir == "/tmp/archon-src"
    assert registry.confirmer is confirmer
    assert registry.config is config
    assert registry.mcp_client_cls is None
    assert registry._execute_event_handler is None
    assert registry._worker_session_affinity == {}
    assert registry._session_id == ""
    assert registry.get_schemas() == []


def test_tool_registry_empty_registers_tools_normally():
    registry = ToolRegistry.empty()

    def hello(name: str) -> str:
        return f"hi {name}"

    registry.register(
        "hello",
        "Say hello",
        {
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
        },
        hello,
    )

    assert registry.get_schemas()[0]["name"] == "hello"
    assert registry.execute("hello", {"name": "world"}) == "hi world"


def test_build_subagent_registry_freshly_registers_tools_and_excludes_spawn_subagent():
    parent = ToolRegistry(archon_source_dir="/tmp/archon-src")

    explore = build_subagent_registry(
        subagent_type=get_subagent_type("explore"),
        archon_source_dir="/tmp/archon-src",
        confirmer=lambda *_args, **_kwargs: True,
        config=Config(),
    )

    assert "spawn_subagent" in parent.tools
    assert "spawn_subagent" not in explore.tools
    assert explore.tools["read_file"] is not parent.tools["read_file"]
    assert explore.handlers["read_file"] is not parent.handlers["read_file"]


def test_explore_registry_rejects_dangerous_shell_commands():
    levels = []

    def confirmer(command: str, level: Level) -> bool:
        levels.append(level)
        return True

    explore = build_subagent_registry(
        subagent_type="explore",
        archon_source_dir="/tmp/archon-src",
        confirmer=confirmer,
        config=Config(),
    )

    result = explore.execute("shell", {"command": "rm -rf /"})

    assert result == "Command rejected by safety gate."
    assert levels == []


def test_general_registry_excludes_worker_delegate_and_spawn_tools():
    general = build_subagent_registry(
        subagent_type="general",
        archon_source_dir="/tmp/archon-src",
        confirmer=lambda *_args, **_kwargs: True,
        config=Config(),
    )

    tool_names = set(general.tools)

    assert "spawn_subagent" not in tool_names
    assert "delegate_code_task" not in tool_names
    assert not any(name.startswith("worker_") for name in tool_names)
