"""Integration tests for native subagent usage roll-up."""

from __future__ import annotations

from unittest.mock import MagicMock

import archon.agent as agent_module

from archon.config import Config
from archon.agent import Agent
from archon.tools import ToolRegistry


def _make_agent() -> Agent:
    llm = MagicMock()
    llm.chat = MagicMock()
    agent = Agent(llm, ToolRegistry.empty(), Config())
    agent._system_prompt = "test prompt"
    return agent


def test_subagent_usage_rolls_into_parent_counters_and_ledger(monkeypatch):
    agent = _make_agent()
    agent.session_id = "sess-parent"
    agent.llm.provider = "anthropic"
    agent.llm.model = "claude-sonnet-4-6"

    persisted_events = []
    usage_events = []

    monkeypatch.setattr(agent_module, "record_usage_event", lambda event: persisted_events.append(event) or True)
    agent.hooks.register("usage.recorded", usage_events.append)

    agent._on_tool_execute_event(
        "subagent_usage",
        {
            "source": "subagent:explore",
            "provider": "anthropic",
            "model": "claude-haiku-4-5-20251001",
            "input_tokens": 30,
            "output_tokens": 10,
            "status": "ok",
            "iterations_used": 3,
        },
    )

    assert agent.total_input_tokens == 30
    assert agent.total_output_tokens == 10
    assert len(persisted_events) == 1
    event = persisted_events[0]
    assert event.session_id == "sess-parent"
    assert event.turn_id == agent.last_turn_id
    assert event.source == "subagent:explore"
    assert event.provider == "anthropic"
    assert event.model == "claude-haiku-4-5-20251001"
    assert event.input_tokens == 30
    assert event.output_tokens == 10
    assert len(usage_events) == 1
    payload = usage_events[0].payload
    assert payload["source"] == "subagent:explore"
    assert payload["session_id"] == "sess-parent"
    assert payload["provider"] == "anthropic"
    assert payload["model"] == "claude-haiku-4-5-20251001"
    assert payload["input_tokens"] == 30
    assert payload["output_tokens"] == 10
    assert payload["recorded"] is True
