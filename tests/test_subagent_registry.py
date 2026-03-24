"""Tests for ToolRegistry.empty()."""

from archon.config import Config
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
