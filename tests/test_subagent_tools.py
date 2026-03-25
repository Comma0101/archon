"""Tests for the native spawn_subagent tool."""

from __future__ import annotations

from unittest.mock import MagicMock

from archon.config import Config
from archon.llm import LLMResponse
from archon.subagents import get_subagent_type
from archon.subagents.runner import SubagentResult
from archon.subagents import tools as subagent_tools
from archon.tools import ToolRegistry


def test_parent_registry_registers_spawn_subagent():
    registry = ToolRegistry(archon_source_dir="/tmp/archon-src")

    assert "spawn_subagent" in registry.tools


def test_spawn_subagent_description_includes_routing_guidance():
    registry = ToolRegistry(archon_source_dir="/tmp/archon-src")
    description = registry.tools["spawn_subagent"]["description"]

    assert 'type="explore"' in description
    assert 'type="general"' in description
    assert "delegate_code_task" in description


def test_spawn_subagent_rejects_invalid_type_and_empty_task():
    registry = ToolRegistry.empty(config=Config())
    subagent_tools.register_subagent_tools(registry)

    assert (
        registry.execute("spawn_subagent", {"type": "invalid", "task": "inspect"})
        == "Error: Unknown subagent type: invalid"
    )
    assert (
        registry.execute("spawn_subagent", {"type": "explore", "task": "   "})
        == "Error: Task cannot be empty."
    )


def test_spawn_subagent_builds_child_registry_with_expected_tier_and_toolset(monkeypatch):
    cfg = Config()
    cfg.llm.provider = "anthropic"
    cfg.llm.api_key = "test-key"
    cfg.llm.base_url = "https://example.test"
    cfg.tiers.light = "light-model"
    cfg.tiers.standard = "standard-model"
    cfg.agent.max_iterations = 8
    registry = ToolRegistry.empty(config=cfg)
    subagent_tools.register_subagent_tools(registry)

    captured: dict[str, object] = {}

    class FakeLLMClient:
        def __init__(
            self,
            provider: str,
            model: str,
            api_key: str,
            temperature: float = 0.3,
            base_url: str = "",
        ) -> None:
            captured["llm"] = {
                "provider": provider,
                "model": model,
                "api_key": api_key,
                "temperature": temperature,
                "base_url": base_url,
            }

    class FakeRunner:
        def __init__(self, llm, tools, config, subagent_type: str, **kwargs) -> None:
            captured["runner_tools"] = set(tools.tools)
            captured["subagent_type"] = subagent_type
            captured["child_registry"] = tools
            self._result = SubagentResult(
                status="ok",
                text="child done",
                input_tokens=12,
                output_tokens=7,
                iterations_used=3,
            )

        def run(self, task: str, context: str = "") -> SubagentResult:
            captured["task"] = task
            captured["context"] = context
            return self._result

    monkeypatch.setattr(subagent_tools, "LLMClient", FakeLLMClient)
    monkeypatch.setattr(subagent_tools, "SubagentRunner", FakeRunner)

    result = registry.execute(
        "spawn_subagent",
        {
            "type": "explore",
            "task": "inspect the repo",
            "context": "recent changes",
        },
    )

    assert captured["llm"] == {
        "provider": "anthropic",
        "model": "light-model",
        "api_key": "test-key",
        "temperature": 0.3,
        "base_url": "https://example.test",
    }
    assert captured["subagent_type"] == "explore"
    assert captured["task"] == "inspect the repo"
    assert captured["context"] == "recent changes"
    assert captured["runner_tools"] == {"read_file", "grep", "glob", "list_dir", "shell"}
    assert "spawn_subagent" not in captured["runner_tools"]
    assert "spawn_subagent" not in captured["child_registry"].tools
    assert "subagent_type: explore" in result
    assert "status: ok" in result
    assert "iterations: 3/8" in result
    assert "tokens: 12 in, 7 out" in result
    assert "child done" in result


def test_spawn_subagent_emits_usage_event_payload(monkeypatch):
    cfg = Config()
    cfg.llm.provider = "anthropic"
    cfg.llm.api_key = "test-key"
    cfg.llm.base_url = "https://example.test"
    cfg.tiers.light = "light-model"
    registry = ToolRegistry.empty(config=cfg)
    subagent_tools.register_subagent_tools(registry)

    class FakeLLMClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

    class FakeRunner:
        def __init__(self, *args, **kwargs) -> None:
            self._result = SubagentResult(
                status="ok",
                text="child done",
                input_tokens=12,
                output_tokens=7,
                iterations_used=3,
            )

        def run(self, task: str, context: str = "") -> SubagentResult:
            return self._result

    events: list[tuple[str, dict]] = []

    monkeypatch.setattr(subagent_tools, "LLMClient", FakeLLMClient)
    monkeypatch.setattr(subagent_tools, "SubagentRunner", FakeRunner)
    registry.set_execute_event_handler(lambda kind, payload: events.append((kind, payload)))

    result = registry.execute(
        "spawn_subagent",
        {
            "type": "explore",
            "task": "inspect the repo",
        },
    )

    assert "status: ok" in result
    usage_payloads = [payload for kind, payload in events if kind == "subagent_usage"]
    assert len(usage_payloads) == 1
    payload = usage_payloads[0]
    assert payload["source"] == "subagent:explore"
    assert payload["provider"] == "anthropic"
    assert payload["model"] == "light-model"
    assert payload["input_tokens"] == 12
    assert payload["output_tokens"] == 7
    assert payload["status"] == "ok"
    assert payload["iterations_used"] == 3


def test_spawn_subagent_emits_usage_event_for_failed_result(monkeypatch):
    cfg = Config()
    cfg.llm.provider = "anthropic"
    cfg.llm.api_key = "test-key"
    cfg.llm.base_url = "https://example.test"
    cfg.tiers.light = "light-model"
    registry = ToolRegistry.empty(config=cfg)
    subagent_tools.register_subagent_tools(registry)

    class FakeLLMClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

    class FakeRunner:
        def __init__(self, *args, **kwargs) -> None:
            self._result = SubagentResult(
                status="failed",
                text="Error: provider unavailable",
                input_tokens=22,
                output_tokens=9,
                iterations_used=1,
            )

        def run(self, task: str, context: str = "") -> SubagentResult:
            return self._result

    events: list[tuple[str, dict]] = []

    monkeypatch.setattr(subagent_tools, "LLMClient", FakeLLMClient)
    monkeypatch.setattr(subagent_tools, "SubagentRunner", FakeRunner)
    registry.set_execute_event_handler(lambda kind, payload: events.append((kind, payload)))

    result = registry.execute(
        "spawn_subagent",
        {
            "type": "explore",
            "task": "inspect the repo",
        },
    )

    assert "status: failed" in result
    usage_payloads = [payload for kind, payload in events if kind == "subagent_usage"]
    assert len(usage_payloads) == 1
    payload = usage_payloads[0]
    assert payload["source"] == "subagent:explore"
    assert payload["input_tokens"] == 22
    assert payload["output_tokens"] == 9
    assert payload["status"] == "failed"
    assert payload["iterations_used"] == 1
