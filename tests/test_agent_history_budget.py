"""Tests for per-iteration history budget enforcement."""

import pytest
from unittest.mock import MagicMock, patch
from archon.agent import Agent, _estimate_history_chars


def _make_config(max_chars=8000, trim_to_chars=4000, max_iterations=5):
    """Build a minimal Config with tight budget for testing."""
    config = MagicMock()
    config.agent.max_iterations = max_iterations
    config.agent.temperature = 0.3
    config.agent.llm_request_timeout_sec = 10
    config.agent.llm_retry_attempts = 1
    config.agent.tool_result_max_chars = 2000
    config.agent.tool_result_worker_max_chars = 1000
    config.agent.history_max_messages = 20
    config.agent.history_trim_to_messages = 10
    config.agent.history_max_chars = max_chars
    config.agent.history_trim_to_chars = trim_to_chars
    config.orchestrator.enabled = False
    config.orchestrator.mode = "legacy"
    config.research.google_deep_research.enabled = False
    config.profiles = {"default": MagicMock(skill="", allowed_tools=["*"], max_mode="implement", allowed_tools_explicit=False, max_mode_explicit=False, execution_backend="host")}
    return config


def test_history_trimmed_within_iteration_loop():
    """History must not exceed budget even during multi-iteration tool loops."""
    config = _make_config(max_chars=8000, trim_to_chars=4000, max_iterations=5)
    llm = MagicMock()
    tools = MagicMock()
    tools.get_schemas.return_value = []

    agent = Agent(llm=llm, tools=tools, config=config)
    agent.history = [
        {"role": "user", "content": "x" * 3000},
        {"role": "assistant", "content": "y" * 3000},
    ]
    assert hasattr(agent, '_enforce_iteration_budget')


def test_enforce_iteration_budget_trims_when_over():
    """_enforce_iteration_budget should trim history when over char budget."""
    config = _make_config(max_chars=4000, trim_to_chars=2000)
    llm = MagicMock()
    tools = MagicMock()
    tools.get_schemas.return_value = []

    agent = Agent(llm=llm, tools=tools, config=config)
    agent.history = [
        {"role": "user", "content": "a" * 1000},
        {"role": "assistant", "content": "b" * 1000},
        {"role": "user", "content": "c" * 1000},
        {"role": "assistant", "content": "d" * 1000},
        {"role": "user", "content": "e" * 1000},
    ]

    agent._enforce_iteration_budget()

    chars_after = _estimate_history_chars(agent.history)
    assert chars_after <= 4000, f"History should be under budget: {chars_after}"
    assert len(agent.history) >= 1


def test_enforce_iteration_budget_noop_when_under():
    """_enforce_iteration_budget should not trim when under budget."""
    config = _make_config(max_chars=8000, trim_to_chars=4000)
    llm = MagicMock()
    tools = MagicMock()
    tools.get_schemas.return_value = []

    agent = Agent(llm=llm, tools=tools, config=config)
    agent.history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    original_len = len(agent.history)

    agent._enforce_iteration_budget()

    assert len(agent.history) == original_len


def test_enforce_iteration_budget_compacts_when_prompt_pressure_is_high(monkeypatch):
    """High prompt pressure should trigger compaction even if char budget is disabled."""
    config = _make_config(max_chars=0, trim_to_chars=0)
    config.agent.prompt_pressure_max_input_tokens = 20000
    llm = MagicMock()
    tools = MagicMock()
    tools.get_schemas.return_value = []

    agent = Agent(llm=llm, tools=tools, config=config)
    agent.last_input_tokens = 32000
    agent.history = [
        {"role": "user", "content": "older context " + ("a" * 120)},
        {"role": "assistant", "content": [{"type": "text", "text": "older answer"}]},
        {"role": "user", "content": "latest question"},
    ]

    compaction_calls = []

    def fake_compact_history(messages, layer="session", summary_id="latest", max_entries=8):
        compaction_calls.append((layer, list(messages)))
        return {
            "path": "compactions/tasks/history-budget.md",
            "layer": "task",
            "summary": "prompt pressure trim",
        }

    monkeypatch.setattr("archon.agent.memory_store.compact_history", fake_compact_history)

    agent._enforce_iteration_budget()

    assert compaction_calls, "expected prompt-pressure budget compaction when prompt usage is high"
    assert any(msg.get("content") == "latest question" for msg in agent.history)


def test_enforce_iteration_budget_prompt_pressure_keeps_latest_tool_round_trip(monkeypatch):
    """Prompt-pressure trimming should preserve the newest tool-use and tool-result pair."""
    config = _make_config(max_chars=0, trim_to_chars=0)
    config.agent.prompt_pressure_max_input_tokens = 20000
    llm = MagicMock()
    tools = MagicMock()
    tools.get_schemas.return_value = []

    agent = Agent(llm=llm, tools=tools, config=config)
    agent.last_input_tokens = 32000
    agent.history = [
        {"role": "user", "content": "older context " + ("a" * 120)},
        {"role": "assistant", "content": [{"type": "text", "text": "older answer"}]},
        {"role": "user", "content": "latest question"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "tc_latest", "name": "shell", "input": {"command": "echo hi"}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tc_latest", "tool_name": "shell", "content": "ok"}],
        },
    ]

    monkeypatch.setattr(
        "archon.agent.memory_store.compact_history",
        lambda messages, layer="session", summary_id="latest", max_entries=8: {
            "path": "compactions/tasks/history-budget.md",
            "layer": "task",
            "summary": "prompt pressure trim",
        },
    )

    agent._enforce_iteration_budget()

    assert not any(msg.get("content") == "older context " + ("a" * 120) for msg in agent.history)
    assert any(
        msg.get("role") == "assistant"
        and isinstance(msg.get("content"), list)
        and any(isinstance(block, dict) and block.get("type") == "tool_use" for block in msg["content"])
        for msg in agent.history
    )
    assert any(
        msg.get("role") == "user"
        and isinstance(msg.get("content"), list)
        and any(isinstance(block, dict) and block.get("type") == "tool_result" for block in msg["content"])
        for msg in agent.history
    )
