"""Tests for typed suspension flow in the turn executor."""

from unittest.mock import MagicMock

from archon.agent import Agent
from archon.config import Config
from archon.execution.contracts import SuspensionRequest
from archon.execution.turn_executor import execute_turn
from archon.llm import LLMResponse, ToolCall
from archon.tools import ToolRegistry


def test_tool_registry_execute_preserves_suspension_request():
    registry = ToolRegistry(archon_source_dir=None)
    request = SuspensionRequest(
        kind="human_input",
        job_id="setup:browser-use",
        question="Provide OPENAI_API_KEY",
        context="Needed to continue browser-use setup.",
        resume_hint="Reply with the key to resume.",
    )
    registry.register(
        "ask_human",
        "Request human input",
        {"properties": {}, "required": []},
        lambda: request,
    )

    result = registry.execute("ask_human", {})

    assert result is request


def test_execute_turn_returns_suspension_request_without_tool_result_history(monkeypatch):
    monkeypatch.setattr("archon.agent.memory_store.capture_preference_to_inbox", lambda *_a, **_k: None)
    monkeypatch.setattr("archon.agent.memory_store.prefetch_for_query", lambda *_a, **_k: [])

    llm = MagicMock()
    llm.chat = MagicMock(
        return_value=LLMResponse(
            text=None,
            tool_calls=[ToolCall(id="tc_suspend", name="ask_human", arguments={})],
            raw_content=[{"type": "tool_use", "id": "tc_suspend", "name": "ask_human", "input": {}}],
            input_tokens=10,
            output_tokens=2,
        )
    )
    tools = ToolRegistry(archon_source_dir=None)
    request = SuspensionRequest(
        kind="human_input",
        job_id="setup:browser-use",
        question="Provide OPENAI_API_KEY",
        context="Needed to continue browser-use setup.",
        resume_hint="Reply with the key to resume.",
    )
    tools.register(
        "ask_human",
        "Request human input",
        {"properties": {}, "required": []},
        lambda: request,
    )
    agent = Agent(llm, tools, Config())
    agent._system_prompt = "test prompt"

    turn_id = agent._next_turn_id()
    agent.last_turn_id = turn_id
    agent.history.append({"role": "user", "content": "learn browser-use"})

    result = execute_turn(
        agent,
        turn_id=turn_id,
        user_message="learn browser-use",
        active_profile="default",
        log_prefix="[turn=t001]",
        turn_system_prompt="test prompt",
        llm_step=lambda prompt: llm.chat(prompt, agent.history, agent.tools.get_schemas()),
    )

    assert result is request
    assert [message["role"] for message in agent.history] == ["user", "assistant"]
