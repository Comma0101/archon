"""Tests for auto-compaction when approaching context limit."""

from unittest.mock import MagicMock, patch
from archon.agent import Agent, _estimate_history_chars


def _make_config():
    config = MagicMock()
    config.agent.max_iterations = 15
    config.agent.temperature = 0.3
    config.agent.llm_request_timeout_sec = 10
    config.agent.llm_retry_attempts = 1
    config.agent.tool_result_max_chars = 2000
    config.agent.tool_result_worker_max_chars = 1000
    config.agent.history_max_messages = 80
    config.agent.history_trim_to_messages = 60
    config.agent.history_max_chars = 10000
    config.agent.history_trim_to_chars = 6000
    config.orchestrator.enabled = False
    config.orchestrator.mode = "legacy"
    config.research.google_deep_research.enabled = False
    config.profiles = {"default": MagicMock(skill="", allowed_tools=["*"], max_mode="implement", allowed_tools_explicit=False, max_mode_explicit=False, execution_backend="host")}
    return config


def test_auto_compact_triggers_at_threshold():
    """When history exceeds max_chars, _enforce_iteration_budget trims it."""
    config = _make_config()
    llm = MagicMock()
    tools = MagicMock()
    tools.get_schemas.return_value = []

    agent = Agent(llm=llm, tools=tools, config=config)
    # Fill history to 8500 chars (85% of 10000 max)
    agent.history = [{"role": "user", "content": "x" * 8500}]

    agent._enforce_iteration_budget()

    # History should still have at least the one message (can't drop below 2)
    # But it should be noted that with only 1 message, the while loop won't trigger (> 2 guard)
    # So let's use multiple messages to trigger actual trimming
    agent.history = [
        {"role": "user", "content": "a" * 4000},
        {"role": "assistant", "content": "b" * 4000},
        {"role": "user", "content": "c" * 4000},
    ]
    agent._enforce_iteration_budget()
    assert _estimate_history_chars(agent.history) <= 10000


def test_auto_compact_no_action_under_threshold():
    """When history is under budget, nothing happens."""
    config = _make_config()
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
