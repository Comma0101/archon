"""Tests for the bounded native subagent runner."""

from unittest.mock import MagicMock

import pytest

from archon.config import Config
from archon.execution.contracts import SuspensionRequest
from archon.llm import LLMResponse, ToolCall
from archon.subagents.runner import SubagentResult, SubagentRunner
from archon.tools import ToolRegistry


def _make_response(
    text: str | None,
    *,
    tool_calls: list[ToolCall] | None = None,
    input_tokens: int = 10,
    output_tokens: int = 4,
) -> LLMResponse:
    raw_content = []
    if text is not None:
        raw_content = [{"type": "text", "text": text}]
    return LLMResponse(
        text=text,
        tool_calls=tool_calls or [],
        raw_content=raw_content,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _make_runner(llm, registry: ToolRegistry, config: Config | None = None) -> SubagentRunner:
    return SubagentRunner(
        llm=llm,
        tools=registry,
        config=config or Config(),
    )


def test_runner_returns_ok_for_simple_text_completion():
    llm = MagicMock()
    llm.chat = MagicMock(return_value=_make_response("done", input_tokens=7, output_tokens=3))
    runner = _make_runner(llm, ToolRegistry.empty())

    result = runner.run("solve the problem")

    assert result == SubagentResult(
        status="ok",
        text="done",
        input_tokens=7,
        output_tokens=3,
        iterations_used=1,
    )
    assert [msg["role"] for msg in runner.history] == ["user", "assistant"]
    assert llm.chat.call_count == 1


def test_runner_tool_call_then_completion_shapes_tool_history():
    llm = MagicMock()
    llm.chat = MagicMock(
        side_effect=[
            _make_response(
                None,
                tool_calls=[
                    ToolCall(id="tool-1", name="shell", arguments={"command": "echo hi"})
                ],
                input_tokens=11,
                output_tokens=5,
            ),
            _make_response("done", input_tokens=13, output_tokens=6),
        ]
    )
    registry = ToolRegistry.empty()
    registry.register(
        "shell",
        "Run a shell command",
        {
            "properties": {
                "command": {"type": "string"},
            },
            "required": ["command"],
        },
        lambda command, _ctx=None: "\n".join(f"line {index}" for index in range(1, 15)) + "\n[exit_code=0]",
    )
    runner = _make_runner(llm, registry)

    result = runner.run("inspect output")

    tool_results = [
        block["content"]
        for msg in runner.history
        if msg["role"] == "user" and isinstance(msg["content"], list)
        for block in msg["content"]
        if block.get("type") == "tool_result"
    ]

    assert result.status == "ok"
    assert result.text == "done"
    assert result.iterations_used == 2
    assert tool_results
    assert "command: echo hi" in tool_results[0]
    assert "output:" in tool_results[0]
    assert "... [4 lines omitted] ..." in tool_results[0]
    assert "line 7" not in tool_results[0]
    assert "line 14" in tool_results[0]
    assert llm.chat.call_count == 2


def test_runner_stops_early_after_repeated_tool_errors():
    llm = MagicMock()
    llm.chat = MagicMock(
        side_effect=[
            _make_response(
                None,
                tool_calls=[ToolCall(id="tool-1", name="shell", arguments={"command": "echo hi"})],
            ),
            _make_response(
                None,
                tool_calls=[ToolCall(id="tool-2", name="shell", arguments={"command": "echo hi"})],
            ),
            _make_response("should not be reached"),
        ]
    )
    config = Config()
    config.agent.max_consecutive_tool_errors = 2
    registry = ToolRegistry.empty()
    registry.register(
        "shell",
        "Run a shell command",
        {
            "properties": {
                "command": {"type": "string"},
            },
            "required": ["command"],
        },
        lambda command, _ctx=None: "Error: boom",
    )
    runner = _make_runner(llm, registry, config=config)

    result = runner.run("keep trying")

    assert result.status == "failed"
    assert result.iterations_used == 2
    assert llm.chat.call_count == 2


def test_runner_wall_clock_timeout_returns_timeout(monkeypatch):
    llm = MagicMock()
    llm.chat = MagicMock(
        side_effect=[
            _make_response(
                None,
                tool_calls=[ToolCall(id="tool-1", name="shell", arguments={"command": "echo hi"})],
            ),
            _make_response("late"),
        ]
    )
    registry = ToolRegistry.empty()
    registry.register(
        "shell",
        "Run a shell command",
        {
            "properties": {
                "command": {"type": "string"},
            },
            "required": ["command"],
        },
        lambda command, _ctx=None: "ok\n[exit_code=0]",
    )
    config = Config()
    config.agent.wall_clock_timeout_sec = 1.0
    runner = _make_runner(llm, registry, config=config)
    times = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr("archon.subagents.runner.time.monotonic", lambda: next(times))

    result = runner.run("timeout please")

    assert result.status == "timeout"
    assert result.iterations_used == 1
    assert llm.chat.call_count == 1


def test_runner_max_iterations_one_still_allows_one_llm_step():
    llm = MagicMock()
    llm.chat = MagicMock(return_value=_make_response("done"))
    config = Config()
    config.agent.max_iterations = 1
    runner = _make_runner(llm, ToolRegistry.empty(), config=config)

    result = runner.run("limit")

    assert result.status == "ok"
    assert result.text == "done"
    assert result.iterations_used == 1
    assert llm.chat.call_count == 1


def test_runner_fails_fast_on_suspension_request():
    llm = MagicMock()
    llm.chat = MagicMock(
        side_effect=[
            _make_response(
                None,
                tool_calls=[ToolCall(id="tool-1", name="shell", arguments={"command": "echo hi"})],
            ),
        ]
    )
    registry = ToolRegistry.empty()
    registry.register(
        "shell",
        "Run a shell command",
        {
            "properties": {
                "command": {"type": "string"},
            },
            "required": ["command"],
        },
        lambda command, _ctx=None: SuspensionRequest(question="Need human input"),
    )
    runner = _make_runner(llm, registry)

    result = runner.run("suspend")

    assert result.status == "failed"
    assert "suspension" in result.text.lower()
    assert result.iterations_used == 1


def test_runner_denies_policy_blocked_tools_before_execution(monkeypatch):
    llm = MagicMock()
    llm.chat = MagicMock(
        side_effect=[
            _make_response(
                None,
                tool_calls=[ToolCall(id="tool-1", name="shell", arguments={"command": "rm -rf /"})],
            ),
        ]
    )
    registry = ToolRegistry.empty()
    called = {"n": 0}

    def _shell(command, _ctx=None):
        called["n"] += 1
        return "should not run"

    registry.register(
        "shell",
        "Run a shell command",
        {
            "properties": {
                "command": {"type": "string"},
            },
            "required": ["command"],
        },
        _shell,
    )
    runner = _make_runner(llm, registry)
    monkeypatch.setattr(
        "archon.subagents.runner.evaluate_tool_policy",
        lambda **_kwargs: MagicMock(decision="deny", reason="not allowed", profile="default"),
    )

    result = runner.run("blocked")

    assert result.status == "failed"
    assert called["n"] == 0


def test_runner_appends_shaped_tool_results_to_history():
    llm = MagicMock()
    llm.chat = MagicMock(
        side_effect=[
            _make_response(
                None,
                tool_calls=[ToolCall(id="tool-1", name="read_file", arguments={"path": "/tmp/example.py", "offset": 0, "limit": 12})],
            ),
            _make_response("done"),
        ]
    )
    registry = ToolRegistry.empty()
    registry.register(
        "read_file",
        "Read a file",
        {
            "properties": {
                "path": {"type": "string"},
                "offset": {"type": "integer", "default": 0},
                "limit": {"type": "integer", "default": 12},
            },
            "required": ["path"],
        },
        lambda path, offset=0, limit=12, _ctx=None: "\n".join(f"line {index}" for index in range(1, 20)),
    )
    runner = _make_runner(llm, registry)

    result = runner.run("inspect")
    tool_result_messages = [
        msg for msg in runner.history if msg["role"] == "user" and isinstance(msg["content"], list)
    ]

    assert result.status == "ok"
    assert tool_result_messages
    shaped = tool_result_messages[0]["content"][0]["content"]
    assert "path: /tmp/example.py" in shaped
    assert "excerpt:" in shaped
    assert "... [7 lines omitted] ..." in shaped
    assert "line 19" not in shaped


def test_runner_rejects_unsupported_deep_research_job_before_llm_call():
    llm = MagicMock()
    registry = ToolRegistry.empty()
    registry.register(
        "read_file",
        "Read a file",
        {"properties": {"path": {"type": "string"}}, "required": ["path"]},
        lambda path, _ctx=None: "ok",
    )
    runner = _make_runner(llm, registry)

    result = runner.run("start a deep research job")

    assert result.status == "failed"
    assert "deep research" in result.text.lower()
    assert result.iterations_used == 0
    assert llm.chat.call_count == 0


def test_runner_rejects_unsupported_worker_session_before_llm_call():
    llm = MagicMock()
    registry = ToolRegistry.empty()
    registry.register(
        "read_file",
        "Read a file",
        {"properties": {"path": {"type": "string"}}, "required": ["path"]},
        lambda path, _ctx=None: "ok",
    )
    runner = _make_runner(llm, registry)

    result = runner.run("start a worker session in the background")

    assert result.status == "failed"
    assert "worker" in result.text.lower() or "background" in result.text.lower()
    assert result.iterations_used == 0
    assert llm.chat.call_count == 0


def test_runner_returns_structured_failure_when_later_llm_step_raises(monkeypatch):
    first_response = _make_response(
        None,
        tool_calls=[ToolCall(id="tool-1", name="read_file", arguments={"path": "/tmp/example.py"})],
        input_tokens=11,
        output_tokens=5,
    )
    calls = {"n": 0}

    def _fake_chat_with_retry(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return first_response
        raise RuntimeError("503 service unavailable")

    monkeypatch.setattr("archon.subagents.runner._chat_with_retry", _fake_chat_with_retry)

    llm = MagicMock()
    registry = ToolRegistry.empty()
    registry.register(
        "read_file",
        "Read a file",
        {"properties": {"path": {"type": "string"}}, "required": ["path"]},
        lambda path, _ctx=None: "line 1\nline 2",
    )
    runner = _make_runner(llm, registry)

    result = runner.run("inspect")

    assert result.status == "failed"
    assert "503" in result.text
    assert result.input_tokens == 11
    assert result.output_tokens == 5
    assert result.iterations_used == 1
    assert calls["n"] == 2
