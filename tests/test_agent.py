"""Tests for the agent core loop."""

import threading
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

import archon.control.orchestrator as orchestrator_module
from archon.agent import Agent, _chat_with_retry, _print_tool_call, _print_tool_result
from archon.llm import LLMResponse, ToolCall
from archon.tools import ToolRegistry
from archon.config import Config, ProfileConfig


def make_agent(responses: list[LLMResponse], stream_chunks: list | None = None) -> Agent:
    """Create an agent with a mock LLM that returns the given responses in order.

    If stream_chunks is provided, it sets up chat_stream to yield those chunks.
    Each entry should be a list of (str | LLMResponse) items for one call.
    """
    llm = MagicMock()
    llm.chat = MagicMock(side_effect=responses)

    if stream_chunks is not None:
        def _stream_side_effect(*args, **kwargs):
            chunks = stream_chunks.pop(0)
            yield from chunks
        llm.chat_stream = MagicMock(side_effect=_stream_side_effect)

    tools = ToolRegistry(archon_source_dir=None)
    config = Config()
    agent = Agent(llm, tools, config)
    agent._system_prompt = "test prompt"  # Skip building real prompt
    return agent


def expected_route_payload(
    *,
    turn_id: str,
    mode: str,
    path: str,
    lane: str = "operator",
    reason: str = "static_default_until_classifier",
) -> dict:
    return {
        "turn_id": turn_id,
        "mode": mode,
        "path": path,
        "lane": lane,
        "reason": reason,
        "surface": "terminal",
        "skill": "default",
    }


def assert_tool_sequence_well_formed(messages: list[dict]) -> None:
    """Ensure tool-use and tool-result turns are properly paired in order."""
    for i, msg in enumerate(messages):
        content = msg.get("content")
        if msg.get("role") == "assistant" and isinstance(content, list):
            has_tool_use = any(isinstance(b, dict) and b.get("type") == "tool_use" for b in content)
            if has_tool_use:
                assert i > 0
                assert messages[i - 1].get("role") == "user"
                assert i + 1 < len(messages)
                next_content = messages[i + 1].get("content")
                assert messages[i + 1].get("role") == "user"
                assert isinstance(next_content, list)
                assert any(
                    isinstance(b, dict) and b.get("type") == "tool_result"
                    for b in next_content
                )
        if msg.get("role") == "user" and isinstance(content, list):
            has_tool_result = any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
            if has_tool_result:
                assert i > 0
                prev_content = messages[i - 1].get("content")
                assert messages[i - 1].get("role") == "assistant"
                assert isinstance(prev_content, list)
                assert any(
                    isinstance(b, dict) and b.get("type") == "tool_use"
                    for b in prev_content
                )


class TestAgentLoop:
    def test_simple_text_response(self):
        responses = [
            LLMResponse(text="Hello!", tool_calls=[], raw_content=[{"type": "text", "text": "Hello!"}],
                       input_tokens=10, output_tokens=5),
        ]
        agent = make_agent(responses)
        result = agent.run("hi")
        assert result == "Hello!"
        assert len(agent.history) == 2  # user + assistant

    def test_tool_call_then_response(self):
        responses = [
            # First: tool call
            LLMResponse(
                text=None,
                tool_calls=[ToolCall(id="tc_1", name="shell", arguments={"command": "echo hi"})],
                raw_content=[{"type": "tool_use", "id": "tc_1", "name": "shell", "input": {"command": "echo hi"}}],
                input_tokens=20, output_tokens=10,
            ),
            # Second: text response
            LLMResponse(
                text="Done!",
                tool_calls=[],
                raw_content=[{"type": "text", "text": "Done!"}],
                input_tokens=30, output_tokens=5,
            ),
        ]
        agent = make_agent(responses)
        result = agent.run("run echo hi")
        assert result == "Done!"
        # History: user, assistant (tool_use), user (tool_result), assistant (text)
        assert len(agent.history) == 4

    def test_truncates_large_worker_tool_result_before_adding_to_history(self, monkeypatch):
        responses = [
            LLMResponse(
                text=None,
                tool_calls=[ToolCall(id="tc_delegate", name="delegate_code_task", arguments={"task": "x"})],
                raw_content=[{"type": "tool_use", "id": "tc_delegate", "name": "delegate_code_task", "input": {"task": "x"}}],
                input_tokens=20,
                output_tokens=10,
            ),
            LLMResponse(
                text="Done!",
                tool_calls=[],
                raw_content=[{"type": "text", "text": "Done!"}],
                input_tokens=30,
                output_tokens=5,
            ),
        ]
        agent = make_agent(responses)
        monkeypatch.setattr("archon.agent.memory_store.capture_preference_to_inbox", lambda *_a, **_k: None)
        monkeypatch.setattr("archon.agent.memory_store.prefetch_for_query", lambda *_a, **_k: [])
        agent.tool_result_max_chars = 2000
        agent.tool_result_worker_max_chars = 300

        agent.tools.register(
            "delegate_code_task",
            "Large worker output for truncation test",
            {"properties": {"task": {"type": "string"}}, "required": ["task"]},
            lambda task: "W" * 2000,
        )

        result = agent.run("run worker")

        assert result == "Done!"
        tool_result = agent.history[2]["content"][0]["content"]
        assert isinstance(tool_result, str)
        assert len(tool_result) <= 300
        assert "archon truncated tool result" in tool_result

    def test_hooks_emit_pre_and_post_tool_events_on_success(self, monkeypatch):
        responses = [
            LLMResponse(
                text=None,
                tool_calls=[ToolCall(id="tc_hook", name="hook_echo", arguments={"text": "hi"})],
                raw_content=[{"type": "tool_use", "id": "tc_hook", "name": "hook_echo", "input": {"text": "hi"}}],
                input_tokens=8,
                output_tokens=3,
            ),
            LLMResponse(
                text="done",
                tool_calls=[],
                raw_content=[{"type": "text", "text": "done"}],
                input_tokens=10,
                output_tokens=2,
            ),
        ]
        agent = make_agent(responses)
        monkeypatch.setattr("archon.agent.memory_store.capture_preference_to_inbox", lambda *_a, **_k: None)
        monkeypatch.setattr("archon.agent.memory_store.prefetch_for_query", lambda *_a, **_k: [])
        agent.tools.register(
            "hook_echo",
            "Echo tool for hook tests",
            {
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            lambda text: f"ECHO:{text}",
        )
        events = []
        for kind in ("pre_tool", "tool_registry.pre_execute", "tool_registry.post_execute", "post_tool"):
            agent.hooks.register(kind, events.append)

        result = agent.run("test hooks")

        assert result == "done"
        assert [event.kind for event in events] == [
            "pre_tool",
            "tool_registry.pre_execute",
            "tool_registry.post_execute",
            "post_tool",
        ]
        assert all(event.task_id == "t001" for event in events)
        assert events[0].payload["name"] == "hook_echo"
        assert events[2].payload["status"] == "ok"
        assert events[3].payload["result_is_error"] is False

    def test_hooks_mark_tool_errors_without_breaking_turn(self, monkeypatch):
        responses = [
            LLMResponse(
                text=None,
                tool_calls=[ToolCall(id="tc_fail", name="hook_fail", arguments={})],
                raw_content=[{"type": "tool_use", "id": "tc_fail", "name": "hook_fail", "input": {}}],
                input_tokens=8,
                output_tokens=3,
            ),
            LLMResponse(
                text="done",
                tool_calls=[],
                raw_content=[{"type": "text", "text": "done"}],
                input_tokens=10,
                output_tokens=2,
            ),
        ]
        agent = make_agent(responses)
        monkeypatch.setattr("archon.agent.memory_store.capture_preference_to_inbox", lambda *_a, **_k: None)
        monkeypatch.setattr("archon.agent.memory_store.prefetch_for_query", lambda *_a, **_k: [])

        def _failing_tool():
            raise ValueError("boom")

        agent.tools.register(
            "hook_fail",
            "Failing tool for hook tests",
            {
                "properties": {},
                "required": [],
            },
            _failing_tool,
        )
        events = []
        for kind in ("pre_tool", "tool_registry.pre_execute", "tool_registry.post_execute", "post_tool"):
            agent.hooks.register(kind, events.append)

        result = agent.run("test hook error")

        assert result == "done"
        assert [event.kind for event in events] == [
            "pre_tool",
            "tool_registry.pre_execute",
            "tool_registry.post_execute",
            "post_tool",
        ]
        assert events[2].payload["status"] == "error"
        assert events[2].payload["error_type"] == "ValueError"
        assert events[3].payload["result_is_error"] is True

    def test_policy_shadow_deny_emits_event_but_allows_execution(self, monkeypatch):
        responses = [
            LLMResponse(
                text=None,
                tool_calls=[ToolCall(id="tc_shadow", name="shadow_tool", arguments={"text": "hi"})],
                raw_content=[{"type": "tool_use", "id": "tc_shadow", "name": "shadow_tool", "input": {"text": "hi"}}],
                input_tokens=8,
                output_tokens=3,
            ),
            LLMResponse(
                text="done",
                tool_calls=[],
                raw_content=[{"type": "text", "text": "done"}],
                input_tokens=10,
                output_tokens=2,
            ),
        ]
        agent = make_agent(responses)
        monkeypatch.setattr("archon.agent.memory_store.capture_preference_to_inbox", lambda *_a, **_k: None)
        monkeypatch.setattr("archon.agent.memory_store.prefetch_for_query", lambda *_a, **_k: [])
        agent.config.profiles["default"].allowed_tools = ["read_file"]
        executed = []

        def _shadow_tool(text: str) -> str:
            executed.append(text)
            return f"OK:{text}"

        agent.tools.register(
            "shadow_tool",
            "Policy shadow test tool",
            {
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            _shadow_tool,
        )
        decisions = []
        agent.hooks.register("policy.decision", decisions.append)

        result = agent.run("shadow policy run")

        assert result == "done"
        assert executed == ["hi"]
        assert len(decisions) == 1
        assert decisions[0].payload["decision"] == "shadow_deny"
        assert decisions[0].payload["reason"] == "tool_not_allowed"

    def test_policy_enforced_deny_blocks_tool_execution(self, monkeypatch):
        responses = [
            LLMResponse(
                text=None,
                tool_calls=[ToolCall(id="tc_deny", name="deny_tool", arguments={})],
                raw_content=[{"type": "tool_use", "id": "tc_deny", "name": "deny_tool", "input": {}}],
                input_tokens=8,
                output_tokens=3,
            ),
            LLMResponse(
                text="done",
                tool_calls=[],
                raw_content=[{"type": "text", "text": "done"}],
                input_tokens=10,
                output_tokens=2,
            ),
        ]
        agent = make_agent(responses)
        monkeypatch.setattr("archon.agent.memory_store.capture_preference_to_inbox", lambda *_a, **_k: None)
        monkeypatch.setattr("archon.agent.memory_store.prefetch_for_query", lambda *_a, **_k: [])
        agent.config.orchestrator.enabled = True
        agent.config.orchestrator.mode = "hybrid"
        agent.config.orchestrator.shadow_eval = False
        agent.config.profiles["default"].allowed_tools = ["read_file"]
        executed = {"count": 0}

        def _deny_tool() -> str:
            executed["count"] += 1
            return "should not run"

        agent.tools.register(
            "deny_tool",
            "Policy deny test tool",
            {
                "properties": {},
                "required": [],
            },
            _deny_tool,
        )
        decisions = []
        post_tool_events = []
        agent.hooks.register("policy.decision", decisions.append)
        agent.hooks.register("post_tool", post_tool_events.append)

        result = agent.run("enforced policy run")

        assert result == "done"
        assert executed["count"] == 0
        assert len(decisions) == 1
        assert decisions[0].payload["decision"] == "deny"
        assert decisions[0].payload["reason"] == "tool_not_allowed"
        assert len(post_tool_events) == 1
        assert post_tool_events[0].payload["policy_decision"] == "deny"
        assert post_tool_events[0].payload["result_is_error"] is True

    def test_policy_enforced_deny_blocks_tool_execution_with_uppercase_hybrid_mode(self, monkeypatch):
        responses = [
            LLMResponse(
                text=None,
                tool_calls=[ToolCall(id="tc_deny_case", name="deny_tool_case", arguments={})],
                raw_content=[{"type": "tool_use", "id": "tc_deny_case", "name": "deny_tool_case", "input": {}}],
                input_tokens=8,
                output_tokens=3,
            ),
            LLMResponse(
                text="done",
                tool_calls=[],
                raw_content=[{"type": "text", "text": "done"}],
                input_tokens=10,
                output_tokens=2,
            ),
        ]
        agent = make_agent(responses)
        monkeypatch.setattr("archon.agent.memory_store.capture_preference_to_inbox", lambda *_a, **_k: None)
        monkeypatch.setattr("archon.agent.memory_store.prefetch_for_query", lambda *_a, **_k: [])
        agent.config.orchestrator.enabled = True
        agent.config.orchestrator.mode = "HYBRID"
        agent.config.orchestrator.shadow_eval = False
        agent.config.profiles["default"].allowed_tools = ["read_file"]
        executed = {"count": 0}

        def _deny_tool_case() -> str:
            executed["count"] += 1
            return "should not run"

        agent.tools.register(
            "deny_tool_case",
            "Policy deny test tool with uppercase hybrid mode",
            {
                "properties": {},
                "required": [],
            },
            _deny_tool_case,
        )
        decisions = []
        agent.hooks.register("policy.decision", decisions.append)

        result = agent.run("enforced policy run with uppercase mode")

        assert result == "done"
        assert executed["count"] == 0
        assert len(decisions) == 1
        assert decisions[0].payload["decision"] == "deny"
        assert decisions[0].payload["reason"] == "tool_not_allowed"

    def test_policy_uses_session_selected_profile(self, monkeypatch):
        responses = [
            LLMResponse(
                text=None,
                tool_calls=[ToolCall(id="tc_session_profile", name="session_profile_tool", arguments={})],
                raw_content=[{"type": "tool_use", "id": "tc_session_profile", "name": "session_profile_tool", "input": {}}],
                input_tokens=8,
                output_tokens=3,
            ),
            LLMResponse(
                text="done",
                tool_calls=[],
                raw_content=[{"type": "text", "text": "done"}],
                input_tokens=10,
                output_tokens=2,
            ),
        ]
        agent = make_agent(responses)
        monkeypatch.setattr("archon.agent.memory_store.capture_preference_to_inbox", lambda *_a, **_k: None)
        monkeypatch.setattr("archon.agent.memory_store.prefetch_for_query", lambda *_a, **_k: [])
        agent.config.profiles["default"] = ProfileConfig(allowed_tools=["read_file"])
        agent.config.profiles["safe"] = ProfileConfig(allowed_tools=["session_profile_tool"])
        agent.set_policy_profile("safe")
        executed = {"count": 0}

        def _tool() -> str:
            executed["count"] += 1
            return "ok"

        agent.tools.register(
            "session_profile_tool",
            "Session profile test tool",
            {"properties": {}, "required": []},
            _tool,
        )
        decisions = []
        agent.hooks.register("policy.decision", decisions.append)

        result = agent.run("session profile")

        assert result == "done"
        assert executed["count"] == 1
        assert decisions[0].payload["decision"] == "allow"
        assert decisions[0].payload["profile"] == "safe"

    def test_policy_turn_override_profile_beats_session_profile(self, monkeypatch):
        responses = [
            LLMResponse(
                text=None,
                tool_calls=[ToolCall(id="tc_override", name="override_profile_tool", arguments={})],
                raw_content=[{"type": "tool_use", "id": "tc_override", "name": "override_profile_tool", "input": {}}],
                input_tokens=8,
                output_tokens=3,
            ),
            LLMResponse(
                text="done",
                tool_calls=[],
                raw_content=[{"type": "text", "text": "done"}],
                input_tokens=10,
                output_tokens=2,
            ),
        ]
        agent = make_agent(responses)
        monkeypatch.setattr("archon.agent.memory_store.capture_preference_to_inbox", lambda *_a, **_k: None)
        monkeypatch.setattr("archon.agent.memory_store.prefetch_for_query", lambda *_a, **_k: [])
        agent.config.profiles["default"] = ProfileConfig(allowed_tools=["override_profile_tool"])
        agent.config.profiles["safe"] = ProfileConfig(allowed_tools=["read_file"])
        agent.set_policy_profile("safe")
        executed = {"count": 0}

        def _tool() -> str:
            executed["count"] += 1
            return "ok"

        agent.tools.register(
            "override_profile_tool",
            "Turn override test tool",
            {"properties": {}, "required": []},
            _tool,
        )
        decisions = []
        agent.hooks.register("policy.decision", decisions.append)

        result = agent.run("turn override", policy_profile="default")

        assert result == "done"
        assert executed["count"] == 1
        assert decisions[0].payload["decision"] == "allow"
        assert decisions[0].payload["profile"] == "default"

    def test_policy_falls_back_to_orchestrator_default_profile(self, monkeypatch):
        responses = [
            LLMResponse(
                text=None,
                tool_calls=[ToolCall(id="tc_cfg_profile", name="cfg_profile_tool", arguments={})],
                raw_content=[{"type": "tool_use", "id": "tc_cfg_profile", "name": "cfg_profile_tool", "input": {}}],
                input_tokens=8,
                output_tokens=3,
            ),
            LLMResponse(
                text="done",
                tool_calls=[],
                raw_content=[{"type": "text", "text": "done"}],
                input_tokens=10,
                output_tokens=2,
            ),
        ]
        agent = make_agent(responses)
        monkeypatch.setattr("archon.agent.memory_store.capture_preference_to_inbox", lambda *_a, **_k: None)
        monkeypatch.setattr("archon.agent.memory_store.prefetch_for_query", lambda *_a, **_k: [])
        agent.policy_profile = ""
        agent.config.orchestrator.default_profile = "safe"
        agent.config.profiles["default"] = ProfileConfig(allowed_tools=["read_file"])
        agent.config.profiles["safe"] = ProfileConfig(allowed_tools=["cfg_profile_tool"])
        executed = {"count": 0}

        def _tool() -> str:
            executed["count"] += 1
            return "ok"

        agent.tools.register(
            "cfg_profile_tool",
            "Config default profile tool",
            {"properties": {}, "required": []},
            _tool,
        )
        decisions = []
        agent.hooks.register("policy.decision", decisions.append)

        result = agent.run("cfg profile")

        assert result == "done"
        assert executed["count"] == 1
        assert decisions[0].payload["decision"] == "allow"
        assert decisions[0].payload["profile"] == "safe"

    def test_orchestrator_legacy_mode_runs_without_hybrid_wrapper(self, monkeypatch):
        responses = [
            LLMResponse(
                text="ok",
                tool_calls=[],
                raw_content=[{"type": "text", "text": "ok"}],
                input_tokens=1,
                output_tokens=1,
            )
        ]
        agent = make_agent(responses)
        monkeypatch.setattr("archon.agent.memory_store.capture_preference_to_inbox", lambda *_a, **_k: None)
        monkeypatch.setattr("archon.agent.memory_store.prefetch_for_query", lambda *_a, **_k: [])
        called = {"hybrid": 0}

        def _never_called(**kwargs):
            called["hybrid"] += 1
            raise AssertionError("hybrid runner should not be used in legacy mode")

        monkeypatch.setattr("archon.control.orchestrator._run_hybrid_response", _never_called)

        result = agent.run("legacy mode")

        assert result == "ok"
        assert called["hybrid"] == 0
        assert agent.llm.chat.call_count == 1

    def test_orchestrator_hybrid_mode_calls_planner_wrapper(self, monkeypatch):
        responses = [
            LLMResponse(
                text="ok",
                tool_calls=[],
                raw_content=[{"type": "text", "text": "ok"}],
                input_tokens=1,
                output_tokens=1,
            )
        ]
        agent = make_agent(responses)
        monkeypatch.setattr("archon.agent.memory_store.capture_preference_to_inbox", lambda *_a, **_k: None)
        monkeypatch.setattr("archon.agent.memory_store.prefetch_for_query", lambda *_a, **_k: [])
        agent.config.orchestrator.enabled = True
        agent.config.orchestrator.mode = "hybrid"
        called = {"hybrid": 0}

        def _hybrid_wrapper(*, turn_id, run_legacy, emit_hook):
            called["hybrid"] += 1
            return run_legacy()

        monkeypatch.setattr("archon.control.orchestrator._run_hybrid_response", _hybrid_wrapper)

        result = agent.run("hybrid mode")

        assert result == "ok"
        assert called["hybrid"] == 1
        assert agent.llm.chat.call_count == 1

    def test_orchestrator_hybrid_falls_back_to_legacy_on_error(self, monkeypatch):
        responses = [
            LLMResponse(
                text="ok",
                tool_calls=[],
                raw_content=[{"type": "text", "text": "ok"}],
                input_tokens=1,
                output_tokens=1,
            )
        ]
        agent = make_agent(responses)
        monkeypatch.setattr("archon.agent.memory_store.capture_preference_to_inbox", lambda *_a, **_k: None)
        monkeypatch.setattr("archon.agent.memory_store.prefetch_for_query", lambda *_a, **_k: [])
        agent.config.orchestrator.enabled = True
        agent.config.orchestrator.mode = "hybrid"
        fallback_events = []
        agent.hooks.register("orchestrator.fallback", fallback_events.append)

        def _broken_hybrid(**kwargs):
            raise RuntimeError("planner exploded")

        monkeypatch.setattr("archon.control.orchestrator._run_hybrid_response", _broken_hybrid)

        result = agent.run("hybrid fallback")

        assert result == "ok"
        assert agent.llm.chat.call_count == 1
        assert len(fallback_events) == 1
        assert fallback_events[0].payload["fallback"] == "legacy"
        assert fallback_events[0].payload["error_type"] == "RuntimeError"

    @pytest.mark.parametrize(
        ("user_message", "expected_lane", "expected_reason"),
        [
            ("hi there", "fast", "simple_chat"),
            ("show me git status", "operator", "bounded_file_or_status_request"),
            (
                "do a deep review of the whole repo",
                "job",
                "broad_or_delegated_request",
            ),
            (
                "can you try delegating this task",
                "job",
                "broad_or_delegated_request",
            ),
            (
                "please hand this off",
                "job",
                "broad_or_delegated_request",
            ),
            (
                "run this in background",
                "job",
                "broad_or_delegated_request",
            ),
            (
                "do a deep review of the whole repo and delegate anything that needs follow-up",
                "job",
                "broad_or_delegated_request",
            ),
        ],
    )
    def test_orchestrator_hybrid_route_hook_classifies_lane(
        self,
        monkeypatch,
        user_message,
        expected_lane,
        expected_reason,
    ):
        responses = [
            LLMResponse(
                text="ok",
                tool_calls=[],
                raw_content=[{"type": "text", "text": "ok"}],
                input_tokens=1,
                output_tokens=1,
            )
        ]
        agent = make_agent(responses)
        monkeypatch.setattr("archon.agent.memory_store.capture_preference_to_inbox", lambda *_a, **_k: None)
        monkeypatch.setattr("archon.agent.memory_store.prefetch_for_query", lambda *_a, **_k: [])
        agent.config.orchestrator.enabled = True
        agent.config.orchestrator.mode = "hybrid"
        route_events = []
        agent.hooks.register("orchestrator.route", route_events.append)

        result = agent.run(user_message)

        assert result == "ok"
        assert len(route_events) == 1
        assert route_events[0].payload == expected_route_payload(
            turn_id="t001",
            mode="hybrid",
            path="hybrid_planner_v0",
            lane=expected_lane,
            reason=expected_reason,
        )

    @pytest.mark.parametrize(
        "user_message",
        [
            "my skillset needs work",
            "this is difficult to explain",
            "show the report summary",
            "what is your relationship status",
        ],
    )
    def test_orchestrator_hybrid_route_avoids_operator_false_positives(
        self,
        monkeypatch,
        user_message,
    ):
        responses = [
            LLMResponse(
                text="ok",
                tool_calls=[],
                raw_content=[{"type": "text", "text": "ok"}],
                input_tokens=1,
                output_tokens=1,
            )
        ]
        agent = make_agent(responses)
        monkeypatch.setattr("archon.agent.memory_store.capture_preference_to_inbox", lambda *_a, **_k: None)
        monkeypatch.setattr("archon.agent.memory_store.prefetch_for_query", lambda *_a, **_k: [])
        agent.config.orchestrator.enabled = True
        agent.config.orchestrator.mode = "hybrid"
        route_events = []
        agent.hooks.register("orchestrator.route", route_events.append)

        result = agent.run(user_message)

        assert result == "ok"
        assert len(route_events) == 1
        assert route_events[0].payload == expected_route_payload(
            turn_id="t001",
            mode="hybrid",
            path="hybrid_planner_v0",
            lane="fast",
            reason="simple_chat",
        )

    def test_orchestrator_hybrid_route_ignores_negated_delegate_phrase(self, monkeypatch):
        responses = [
            LLMResponse(
                text="ok",
                tool_calls=[],
                raw_content=[{"type": "text", "text": "ok"}],
                input_tokens=1,
                output_tokens=1,
            )
        ]
        agent = make_agent(responses)
        monkeypatch.setattr("archon.agent.memory_store.capture_preference_to_inbox", lambda *_a, **_k: None)
        monkeypatch.setattr("archon.agent.memory_store.prefetch_for_query", lambda *_a, **_k: [])
        agent.config.orchestrator.enabled = True
        agent.config.orchestrator.mode = "hybrid"
        route_events = []
        agent.hooks.register("orchestrator.route", route_events.append)

        result = agent.run("I don't want to delegate this, just answer directly.")

        assert result == "ok"
        assert len(route_events) == 1
        assert route_events[0].payload == expected_route_payload(
            turn_id="t001",
            mode="hybrid",
            path="hybrid_planner_v0",
            lane="fast",
            reason="simple_chat",
        )

    def test_orchestrator_hybrid_stream_route_hook_includes_lane_metadata(self, monkeypatch):
        final_resp = LLMResponse(
            text="ok",
            tool_calls=[],
            raw_content=[{"type": "text", "text": "ok"}],
            input_tokens=1,
            output_tokens=1,
        )
        stream_chunks = [["ok", final_resp]]
        agent = make_agent([], stream_chunks=stream_chunks)
        monkeypatch.setattr("archon.agent.memory_store.capture_preference_to_inbox", lambda *_a, **_k: None)
        monkeypatch.setattr("archon.agent.memory_store.prefetch_for_query", lambda *_a, **_k: [])
        agent.config.orchestrator.enabled = True
        agent.config.orchestrator.mode = "hybrid"
        route_events = []
        agent.hooks.register("orchestrator.route", route_events.append)

        chunks = list(agent.run_stream("hi there"))

        assert chunks == ["ok"]
        assert len(route_events) == 1
        assert route_events[0].payload == expected_route_payload(
            turn_id="t001",
            mode="hybrid",
            path="hybrid_stream_planner_v0",
            lane="fast",
            reason="simple_chat",
        )

    def test_orchestrator_legacy_route_hook_includes_default_metadata(self, monkeypatch):
        responses = [
            LLMResponse(
                text="ok",
                tool_calls=[],
                raw_content=[{"type": "text", "text": "ok"}],
                input_tokens=1,
                output_tokens=1,
            )
        ]
        agent = make_agent(responses)
        monkeypatch.setattr("archon.agent.memory_store.capture_preference_to_inbox", lambda *_a, **_k: None)
        monkeypatch.setattr("archon.agent.memory_store.prefetch_for_query", lambda *_a, **_k: [])
        route_events = []
        agent.hooks.register("orchestrator.route", route_events.append)

        result = agent.run("show me git status")

        assert result == "ok"
        assert len(route_events) == 1
        assert route_events[0].payload == expected_route_payload(
            turn_id="t001",
            mode="legacy",
            path="legacy_direct",
            lane="operator",
            reason="bounded_file_or_status_request",
        )

    def test_route_payload_ignores_future_route_contract_fields(self, monkeypatch):
        @dataclass
        class FutureRouteDecision:
            turn_id: str
            mode: str
            path: str
            lane: str = "operator"
            reason: str = "static_default_until_classifier"
            surface: str = "terminal"
            skill: str = "default"
            future: str = "ignore-me"

        monkeypatch.setattr(orchestrator_module, "RouteDecision", FutureRouteDecision)

        payload = orchestrator_module._route_payload(
            turn_id="t001",
            mode="hybrid",
            path="hybrid_planner_v0",
            lane="operator",
            reason="bounded_file_or_status_request",
        )

        assert payload == expected_route_payload(
            turn_id="t001",
            mode="hybrid",
            path="hybrid_planner_v0",
            lane="operator",
            reason="bounded_file_or_status_request",
        )

    def test_iteration_limit(self):
        # All responses have tool calls, never a text response
        tool_response = LLMResponse(
            text=None,
            tool_calls=[ToolCall(id="tc_loop", name="shell", arguments={"command": "echo loop"})],
            raw_content=[{"type": "tool_use", "id": "tc_loop", "name": "shell", "input": {"command": "echo loop"}}],
            input_tokens=10, output_tokens=10,
        )
        config = Config()
        config.agent.max_iterations = 3
        responses = [tool_response] * 3

        llm = MagicMock()
        llm.chat = MagicMock(side_effect=responses)
        tools = ToolRegistry(archon_source_dir=None)
        agent = Agent(llm, tools, config)
        agent._system_prompt = "test"

        result = agent.run("loop forever")
        assert "Iteration limit" in result

    def test_token_tracking(self):
        responses = [
            LLMResponse(text="ok", tool_calls=[], raw_content=[{"type": "text", "text": "ok"}],
                       input_tokens=100, output_tokens=50),
        ]
        agent = make_agent(responses)
        agent.run("test")
        assert agent.total_input_tokens == 100
        assert agent.total_output_tokens == 50

    def test_reset(self):
        responses = [
            LLMResponse(text="ok", tool_calls=[], raw_content=[{"type": "text", "text": "ok"}],
                       input_tokens=10, output_tokens=5),
        ]
        agent = make_agent(responses)
        agent.run("test")
        assert len(agent.history) > 0
        agent.reset()
        assert len(agent.history) == 0
        assert agent.total_input_tokens == 0

    def test_run_stream_yields_text_deltas(self):
        final_resp = LLMResponse(
            text="Hello world",
            tool_calls=[],
            raw_content=[{"type": "text", "text": "Hello world"}],
            input_tokens=10,
            output_tokens=5,
        )
        stream_chunks = [["Hello", " ", "world", final_resp]]
        agent = make_agent([], stream_chunks=stream_chunks)

        chunks = list(agent.run_stream("hi"))
        assert chunks == ["Hello", " ", "world"]
        assert agent.total_input_tokens == 10
        assert len(agent.history) == 2  # user + assistant

    def test_run_stream_with_tool_calls(self):
        tool_resp = LLMResponse(
            text=None,
            tool_calls=[ToolCall(id="tc_1", name="shell", arguments={"command": "echo hi"})],
            raw_content=[{"type": "tool_use", "id": "tc_1", "name": "shell", "input": {"command": "echo hi"}}],
            input_tokens=20,
            output_tokens=10,
        )
        final_resp = LLMResponse(
            text="Done!",
            tool_calls=[],
            raw_content=[{"type": "text", "text": "Done!"}],
            input_tokens=30,
            output_tokens=5,
        )
        stream_chunks = [
            [tool_resp],            # First call: tool use (no text chunks)
            ["Done!", final_resp],  # Second call: text response
        ]
        agent = make_agent([], stream_chunks=stream_chunks)

        chunks = list(agent.run_stream("run echo hi"))
        assert chunks == ["Done!"]
        assert len(agent.history) == 4

    def test_run_auto_captures_explicit_user_preference_candidate(self, monkeypatch):
        responses = [
            LLMResponse(text="ok", tool_calls=[], raw_content=[{"type": "text", "text": "ok"}],
                       input_tokens=10, output_tokens=5),
        ]
        agent = make_agent(responses)
        captured = {}

        def fake_capture(text, source="user_message"):
            captured["text"] = text
            captured["source"] = source
            return {"id": "mem-1"}

        monkeypatch.setattr("archon.agent.memory_store.capture_preference_to_inbox", fake_capture)

        result = agent.run("I prefer OpenCode for deep code reviews.")
        assert result == "ok"
        assert captured["text"] == "I prefer OpenCode for deep code reviews."
        assert captured["source"] == "user_message"

    def test_run_injects_prefetched_memory_into_turn_system_prompt(self, monkeypatch):
        responses = [
            LLMResponse(text="ok", tool_calls=[], raw_content=[{"type": "text", "text": "ok"}],
                       input_tokens=10, output_tokens=5),
        ]
        agent = make_agent(responses)
        monkeypatch.setattr("archon.agent.memory_store.capture_preference_to_inbox", lambda *_a, **_k: None)
        monkeypatch.setattr(
            "archon.agent.memory_store.prefetch_for_query",
            lambda _q, limit=2: [
                {
                    "path": "profiles/system.md",
                    "kind": "system_profile",
                    "scope": "global",
                    "score": 12.5,
                    "excerpt": "# System Hardware Profile\nGPU RAM storage mounts.",
                }
            ],
        )

        result = agent.run("what do you think about my system")
        assert result == "ok"
        system_prompt_arg = agent.llm.chat.call_args[0][0]
        assert "[Retrieved Memory]" in system_prompt_arg
        assert "profiles/system.md" in system_prompt_arg
        assert "System Hardware Profile" in system_prompt_arg

    def test_tool_trace_includes_log_context_and_turn_id(self, monkeypatch, capsys):
        responses = [
            LLMResponse(
                text=None,
                tool_calls=[ToolCall(id="tc_1", name="shell", arguments={"command": "echo hi"})],
                raw_content=[{"type": "tool_use", "id": "tc_1", "name": "shell", "input": {"command": "echo hi"}}],
                input_tokens=20, output_tokens=10,
            ),
            LLMResponse(
                text="Done!",
                tool_calls=[],
                raw_content=[{"type": "text", "text": "Done!"}],
                input_tokens=30, output_tokens=5,
            ),
        ]
        agent = make_agent(responses)
        monkeypatch.setattr("archon.agent.memory_store.capture_preference_to_inbox", lambda *_a, **_k: None)
        monkeypatch.setattr("archon.agent.memory_store.prefetch_for_query", lambda *_a, **_k: [])
        agent.log_label = "telegram chat=42"

        result = agent.run("run echo hi")
        assert result == "Done!"

        err = capsys.readouterr().err
        assert "telegram chat=42" in err
        assert "turn=t001" in err

    def test_run_retries_transient_llm_error(self, monkeypatch):
        responses = [
            RuntimeError("503 UNAVAILABLE"),
            LLMResponse(
                text="Recovered",
                tool_calls=[],
                raw_content=[{"type": "text", "text": "Recovered"}],
                input_tokens=10,
                output_tokens=4,
            ),
        ]
        llm = MagicMock()
        llm.chat = MagicMock(side_effect=responses)
        tools = ToolRegistry(archon_source_dir=None)
        config = Config()
        agent = Agent(llm, tools, config)
        agent._system_prompt = "test prompt"

        monkeypatch.setattr("archon.agent.memory_store.capture_preference_to_inbox", lambda *_a, **_k: None)
        monkeypatch.setattr("archon.agent.memory_store.prefetch_for_query", lambda *_a, **_k: [])
        slept = []
        monkeypatch.setattr("archon.agent.time.sleep", lambda secs: slept.append(secs))

        result = agent.run("hello")
        assert result == "Recovered"
        assert llm.chat.call_count == 2
        assert slept  # backoff happened

    def test_run_raises_after_primary_retries_exhausted(self, monkeypatch):
        llm = MagicMock()
        llm.chat = MagicMock(side_effect=[
            RuntimeError("503 UNAVAILABLE"),
            RuntimeError("503 UNAVAILABLE"),
            RuntimeError("503 UNAVAILABLE"),
        ])

        tools = ToolRegistry(archon_source_dir=None)
        config = Config()
        agent = Agent(llm, tools, config)
        agent._system_prompt = "test prompt"
        monkeypatch.setattr("archon.agent.memory_store.capture_preference_to_inbox", lambda *_a, **_k: None)
        monkeypatch.setattr("archon.agent.memory_store.prefetch_for_query", lambda *_a, **_k: [])
        monkeypatch.setattr("archon.agent.time.sleep", lambda _secs: None)

        with pytest.raises(RuntimeError, match="503"):
            agent.run("hello")
        assert llm.chat.call_count == 3

    def test_tool_trace_uses_distinct_call_and_result_colors(self, capsys):
        _print_tool_call("shell", {"command": "echo hi"}, prefix="[turn=t001]")
        _print_tool_result("ok", prefix="[turn=t001]")
        err = capsys.readouterr().err
        assert "\x1b[96m" in err  # bright cyan tool call
        assert "\x1b[37m" in err  # readable result lines (white/light gray)

    def test_run_trims_history_at_turn_start(self, monkeypatch):
        responses = [
            LLMResponse(
                text="ok",
                tool_calls=[],
                raw_content=[{"type": "text", "text": "ok"}],
                input_tokens=10,
                output_tokens=5,
            ),
        ]
        agent = make_agent(responses)
        monkeypatch.setattr("archon.agent.memory_store.capture_preference_to_inbox", lambda *_a, **_k: None)
        monkeypatch.setattr("archon.agent.memory_store.prefetch_for_query", lambda *_a, **_k: [])
        agent.history_max_messages = 4
        agent.history_trim_to = 2
        agent.history = [
            {"role": "user", "content": "m1"},
            {"role": "assistant", "content": [{"type": "text", "text": "m2"}]},
            {"role": "user", "content": "m3"},
            {"role": "assistant", "content": [{"type": "text", "text": "m4"}]},
            {"role": "user", "content": "m5"},
        ]

        result = agent.run("hi")

        assert result == "ok"
        # Trim to last 2 old entries, then append new user + final assistant
        assert len(agent.history) == 4
        assert agent.history[0]["content"] == [{"type": "text", "text": "m4"}]
        assert agent.history[1]["content"] == "m5"
        assert agent.history[2]["content"] == "hi"

    def test_run_stream_trims_history_at_turn_start(self, monkeypatch):
        final_resp = LLMResponse(
            text="streamed",
            tool_calls=[],
            raw_content=[{"type": "text", "text": "streamed"}],
            input_tokens=10,
            output_tokens=5,
        )
        stream_chunks = [["streamed", final_resp]]
        agent = make_agent([], stream_chunks=stream_chunks)
        monkeypatch.setattr("archon.agent.memory_store.capture_preference_to_inbox", lambda *_a, **_k: None)
        monkeypatch.setattr("archon.agent.memory_store.prefetch_for_query", lambda *_a, **_k: [])
        agent.history_max_messages = 3
        agent.history_trim_to = 1
        agent.history = [
            {"role": "user", "content": "old1"},
            {"role": "assistant", "content": [{"type": "text", "text": "old2"}]},
            {"role": "user", "content": "old3"},
            {"role": "assistant", "content": [{"type": "text", "text": "old4"}]},
        ]

        chunks = list(agent.run_stream("next"))

        assert chunks == ["streamed"]
        assert len(agent.history) == 3
        assert agent.history[0]["content"] == [{"type": "text", "text": "old4"}]
        assert agent.history[1]["content"] == "next"

    def test_run_stream_retries_transient_llm_error(self, monkeypatch):
        final_resp = LLMResponse(
            text="Recovered stream",
            tool_calls=[],
            raw_content=[{"type": "text", "text": "Recovered stream"}],
            input_tokens=10,
            output_tokens=5,
        )
        llm = MagicMock()

        calls = {"n": 0}

        def _stream_side_effect(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("503 UNAVAILABLE")
            yield "Recovered"
            yield " stream"
            yield final_resp

        llm.chat_stream = MagicMock(side_effect=_stream_side_effect)
        tools = ToolRegistry(archon_source_dir=None)
        config = Config()
        agent = Agent(llm, tools, config)
        agent._system_prompt = "test prompt"

        monkeypatch.setattr("archon.agent.memory_store.capture_preference_to_inbox", lambda *_a, **_k: None)
        monkeypatch.setattr("archon.agent.memory_store.prefetch_for_query", lambda *_a, **_k: [])
        slept = []
        monkeypatch.setattr("archon.agent.time.sleep", lambda secs: slept.append(secs))

        chunks = list(agent.run_stream("hello"))

        assert chunks == ["Recovered", " stream"]
        assert llm.chat_stream.call_count == 2
        assert slept

    def test_run_stream_raises_after_primary_retries_exhausted(self, monkeypatch):
        llm = MagicMock()
        llm.chat_stream = MagicMock(side_effect=RuntimeError("503 UNAVAILABLE"))
        tools = ToolRegistry(archon_source_dir=None)
        config = Config()
        agent = Agent(llm, tools, config)
        agent._system_prompt = "test prompt"
        monkeypatch.setattr("archon.agent.memory_store.capture_preference_to_inbox", lambda *_a, **_k: None)
        monkeypatch.setattr("archon.agent.memory_store.prefetch_for_query", lambda *_a, **_k: [])
        monkeypatch.setattr("archon.agent.time.sleep", lambda _secs: None)

        with pytest.raises(RuntimeError, match="503"):
            list(agent.run_stream("hello"))
        assert llm.chat_stream.call_count == 3

    def test_run_honors_configured_llm_retry_attempts(self, monkeypatch):
        llm = MagicMock()
        llm.chat = MagicMock(side_effect=[
            RuntimeError("503 UNAVAILABLE"),
            LLMResponse(
                text="Recovered",
                tool_calls=[],
                raw_content=[{"type": "text", "text": "Recovered"}],
                input_tokens=10,
                output_tokens=4,
            ),
        ])
        tools = ToolRegistry(archon_source_dir=None)
        config = Config()
        config.agent.llm_retry_attempts = 1
        agent = Agent(llm, tools, config)
        agent._system_prompt = "test prompt"
        monkeypatch.setattr("archon.agent.memory_store.capture_preference_to_inbox", lambda *_a, **_k: None)
        monkeypatch.setattr("archon.agent.memory_store.prefetch_for_query", lambda *_a, **_k: [])
        monkeypatch.setattr("archon.agent.time.sleep", lambda _secs: None)

        with pytest.raises(RuntimeError, match="503"):
            agent.run("hello")
        assert llm.chat.call_count == 1

    def test_chat_with_retry_times_out_without_fallback(self):
        primary = MagicMock()
        gate = threading.Event()

        def _slow_chat(*args, **kwargs):
            gate.wait(0.5)
            return LLMResponse(
                text="late",
                tool_calls=[],
                raw_content=[{"type": "text", "text": "late"}],
                input_tokens=7,
                output_tokens=2,
            )

        primary.chat = MagicMock(side_effect=_slow_chat)

        with pytest.raises(TimeoutError, match="TIMEOUT"):
            _chat_with_retry(
                primary,
                "system",
                [],
                [],
                max_attempts=1,
                request_timeout_sec=0.01,
            )
        assert primary.chat.call_count == 1

    def test_run_trims_history_by_char_budget_even_under_message_limit(self, monkeypatch):
        responses = [
            LLMResponse(
                text="ok",
                tool_calls=[],
                raw_content=[{"type": "text", "text": "ok"}],
                input_tokens=10,
                output_tokens=5,
            ),
        ]
        agent = make_agent(responses)
        monkeypatch.setattr("archon.agent.memory_store.capture_preference_to_inbox", lambda *_a, **_k: None)
        monkeypatch.setattr("archon.agent.memory_store.prefetch_for_query", lambda *_a, **_k: [])
        agent.history_max_messages = 99
        agent.history_trim_to = 50
        agent.history_max_chars = 250
        agent.history_trim_to_chars = 150
        agent.history = [
            {"role": "user", "content": "a" * 100},
            {"role": "assistant", "content": [{"type": "text", "text": "b" * 100}]},
            {"role": "user", "content": "c" * 100},
        ]

        result = agent.run("hi")

        assert result == "ok"
        # Char budget trim should have removed the oldest oversized entries even though
        # message count was under the max. The newest prior entry should remain.
        assert agent.history[0]["content"] == "c" * 100
        assert agent.history[1]["content"] == "hi"
        assert len(agent.history) == 3  # remaining prior + new user + assistant

    def test_run_repairs_tool_sequence_after_message_count_trim(self, monkeypatch):
        response = LLMResponse(
            text="ok",
            tool_calls=[],
            raw_content=[{"type": "text", "text": "ok"}],
            input_tokens=1,
            output_tokens=1,
        )
        llm = MagicMock()
        llm.chat = MagicMock(return_value=response)
        agent = Agent(llm, ToolRegistry(archon_source_dir=None), Config())
        agent._system_prompt = "test prompt"
        monkeypatch.setattr("archon.agent.memory_store.capture_preference_to_inbox", lambda *_a, **_k: None)
        monkeypatch.setattr("archon.agent.memory_store.prefetch_for_query", lambda *_a, **_k: [])
        agent.history_max_messages = 6
        agent.history_trim_to = 4
        agent.history_max_chars = 0
        agent.history_trim_to_chars = 0
        agent.history = [
            {"role": "user", "content": "u0"},
            {"role": "assistant", "content": [{"type": "text", "text": "a0"}]},
            {"role": "user", "content": "u1"},
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "tc_1", "name": "shell", "input": {"command": "echo hi"}}],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tc_1", "tool_name": "shell", "content": "ok"}],
            },
            {"role": "assistant", "content": [{"type": "text", "text": "a1"}]},
            {"role": "user", "content": "u2"},
        ]

        out = agent.run("hello")

        assert out == "ok"
        sent_history = llm.chat.call_args[0][1]
        assert_tool_sequence_well_formed(sent_history)

    def test_run_repairs_orphaned_tool_result_after_char_trim(self, monkeypatch):
        response = LLMResponse(
            text="ok",
            tool_calls=[],
            raw_content=[{"type": "text", "text": "ok"}],
            input_tokens=1,
            output_tokens=1,
        )
        llm = MagicMock()
        llm.chat = MagicMock(return_value=response)
        agent = Agent(llm, ToolRegistry(archon_source_dir=None), Config())
        agent._system_prompt = "test prompt"
        monkeypatch.setattr("archon.agent.memory_store.capture_preference_to_inbox", lambda *_a, **_k: None)
        monkeypatch.setattr("archon.agent.memory_store.prefetch_for_query", lambda *_a, **_k: [])
        agent.history_max_messages = 99
        agent.history_trim_to = 50
        agent.history_max_chars = 160
        agent.history_trim_to_chars = 120
        agent.history = [
            {"role": "user", "content": "u" * 90},
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "tc_2", "name": "shell", "input": {"command": "echo hi"}}],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tc_2", "tool_name": "shell", "content": "ok"}],
            },
            {"role": "assistant", "content": [{"type": "text", "text": "after"}]},
        ]

        out = agent.run("hello")

        assert out == "ok"
        sent_history = llm.chat.call_args[0][1]
        assert_tool_sequence_well_formed(sent_history)

    def test_agent_uses_context_trim_settings_from_config(self):
        config = Config()
        config.agent.history_max_messages = 12
        config.agent.history_trim_to_messages = 9
        config.agent.history_max_chars = 1234
        config.agent.history_trim_to_chars = 1000
        config.agent.llm_retry_attempts = 2
        config.agent.llm_request_timeout_sec = 12

        llm = MagicMock()
        llm.chat = MagicMock()
        tools = ToolRegistry(archon_source_dir=None)

        agent = Agent(llm, tools, config)

        assert agent.history_max_messages == 12
        assert agent.history_trim_to == 9
        assert agent.history_max_chars == 1234
        assert agent.history_trim_to_chars == 1000
        assert agent.llm_retry_attempts == 2
        assert agent.llm_request_timeout_sec == 12

    def test_run_prunes_dangling_assistant_tool_turn_before_next_llm_call(self, monkeypatch):
        response = LLMResponse(
            text="ok",
            tool_calls=[],
            raw_content=[{"type": "text", "text": "ok"}],
            input_tokens=1,
            output_tokens=1,
        )
        llm = MagicMock()
        llm.chat = MagicMock(return_value=response)
        agent = Agent(llm, ToolRegistry(archon_source_dir=None), Config())
        agent._system_prompt = "test prompt"
        agent.history = [
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "tc_1", "name": "shell", "input": {"command": "echo hi"}}],
            },
            {"role": "assistant", "content": [{"type": "text", "text": "stale"}]},
        ]
        monkeypatch.setattr("archon.agent.memory_store.capture_preference_to_inbox", lambda *_a, **_k: None)
        monkeypatch.setattr("archon.agent.memory_store.prefetch_for_query", lambda *_a, **_k: [])

        out = agent.run("hello")

        assert out == "ok"
        sent_history = llm.chat.call_args[0][1]
        assert not any(
            msg.get("role") == "assistant"
            and isinstance(msg.get("content"), list)
            and any(isinstance(b, dict) and b.get("type") == "tool_use" for b in msg.get("content", []))
            for msg in sent_history
        )

    def test_run_rolls_back_assistant_tool_turn_when_tool_phase_raises(self, monkeypatch):
        llm = MagicMock()
        llm.chat = MagicMock(
            return_value=LLMResponse(
                text=None,
                tool_calls=[ToolCall(id="tc_1", name="shell", arguments={"command": "echo hi"})],
                raw_content=[{"type": "tool_use", "id": "tc_1", "name": "shell", "input": {"command": "echo hi"}}],
                input_tokens=1,
                output_tokens=1,
            )
        )
        agent = Agent(llm, ToolRegistry(archon_source_dir=None), Config())
        agent._system_prompt = "test prompt"
        monkeypatch.setattr("archon.agent.memory_store.capture_preference_to_inbox", lambda *_a, **_k: None)
        monkeypatch.setattr("archon.agent.memory_store.prefetch_for_query", lambda *_a, **_k: [])

        def _boom(_name, _args):
            raise RuntimeError("hook failed")

        agent.on_tool_call = _boom

        with pytest.raises(RuntimeError, match="hook failed"):
            agent.run("do it")

        assert not any(
            msg.get("role") == "assistant"
            and isinstance(msg.get("content"), list)
            and any(isinstance(b, dict) and b.get("type") == "tool_use" for b in msg.get("content", []))
            for msg in agent.history
        )
