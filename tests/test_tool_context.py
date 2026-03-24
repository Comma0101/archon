"""Tests for ToolContext plumbing and tool UX events."""

import inspect
import time as time_mod
from pathlib import Path

from archon.tools import ToolRegistry
from archon.ux.events import tool_blocked, tool_diff, tool_end, tool_running
from archon.ux.tool_context import ToolContext


def test_tool_context_fields():
    events = []
    ctx = ToolContext(tool_name="shell", session_id="s1", emit=events.append, meta={})
    assert ctx.tool_name == "shell"
    assert ctx.session_id == "s1"
    assert ctx.meta == {}
    ctx.meta["exit_code"] = 0
    assert ctx.meta["exit_code"] == 0


def test_tool_context_emit_calls_callback():
    events = []
    ctx = ToolContext(tool_name="shell", session_id="s1", emit=events.append, meta={})
    ctx.emit("fake_event")
    assert events == ["fake_event"]


def test_handler_accepts_ctx_detected():
    def with_ctx(command: str, _ctx: ToolContext | None = None) -> str:
        return "ok"

    def without_ctx(command: str) -> str:
        return "ok"

    sig_with = inspect.signature(with_ctx)
    sig_without = inspect.signature(without_ctx)
    assert "_ctx" in sig_with.parameters
    assert "_ctx" not in sig_without.parameters


def test_registry_injects_ctx_when_handler_accepts_it():
    reg = ToolRegistry(archon_source_dir=None)
    reg.set_session_id("s1")
    captured = {}

    def my_tool(name: str, _ctx: ToolContext | None = None) -> str:
        captured["ctx"] = _ctx
        if _ctx is not None:
            _ctx.meta["name"] = name
        return f"hello {name}"

    reg.register(
        "my_tool",
        "test tool",
        {
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        my_tool,
    )

    result = reg.execute("my_tool", {"name": "world"})
    assert result == "hello world"
    assert isinstance(captured["ctx"], ToolContext)
    assert captured["ctx"].tool_name == "my_tool"
    assert captured["ctx"].session_id == "s1"
    assert captured["ctx"].meta["name"] == "world"


def test_registry_skips_ctx_when_handler_does_not_accept_it():
    reg = ToolRegistry(archon_source_dir=None)
    seen = {}

    def plain_tool(name: str) -> str:
        seen["name"] = name
        return f"hello {name}"

    reg.register(
        "plain_tool",
        "test tool",
        {
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        plain_tool,
    )

    result = reg.execute("plain_tool", {"name": "world"})
    assert result == "hello world"
    assert seen == {"name": "world"}


def test_tool_running_output_line():
    evt = tool_running(tool="shell", session_id="s1", detail_type="output_line", line="hello")
    assert evt.kind == "tool_running"
    assert evt.data["tool"] == "shell"
    assert evt.data["session_id"] == "s1"
    assert evt.data["line"] == "hello"


def test_tool_running_heartbeat():
    evt = tool_running(tool="shell", session_id="s1", detail_type="heartbeat", elapsed_s=4.2)
    assert evt.kind == "tool_running"
    assert evt.data["detail_type"] == "heartbeat"
    assert evt.data["elapsed_s"] == 4.2


def test_tool_blocked_event():
    evt = tool_blocked(
        tool="shell",
        session_id="s1",
        command_preview="rm -rf /",
        safety_level="DANGEROUS",
    )
    assert evt.kind == "tool_blocked"
    assert evt.data["tool"] == "shell"
    assert evt.data["command_preview"] == "rm -rf /"
    assert evt.data["safety_level"] == "DANGEROUS"


def test_tool_diff_event():
    evt = tool_diff(
        tool="edit_file",
        session_id="s1",
        path="foo.py",
        diff_text="--- old\n+++ new",
    )
    assert evt.kind == "tool_diff"
    assert evt.data["tool"] == "edit_file"
    assert evt.data["session_id"] == "s1"
    assert evt.data["path"] == "foo.py"
    assert evt.data["diff_text"].startswith("--- old")


def test_tool_end_accepts_session_id():
    evt = tool_end(name="shell", result_summary="shell: exit 0 (5 lines)", session_id="s1")
    assert evt.kind == "tool_end"
    assert evt.data["session_id"] == "s1"
    assert evt.data["name"] == "shell"


class TestHandlerMetadata:
    def test_read_file_post_execute_contains_metadata(self, tmp_path: Path):
        file_path = tmp_path / "hello.txt"
        file_path.write_text("line1\nline2\nline3\n")
        reg = ToolRegistry(
            archon_source_dir=None,
            confirmer=lambda _command, _level: True,
        )
        events = []
        reg.set_execute_event_handler(lambda kind, payload: events.append((kind, payload)))

        result = reg.execute("read_file", {"path": str(file_path)})

        assert "line1" in result
        post_execute = [payload for kind, payload in events if kind == "post_execute"]
        assert len(post_execute) == 1
        assert post_execute[0]["status"] == "ok"
        assert post_execute[0]["meta"]["path"] == str(file_path.resolve())
        assert post_execute[0]["meta"]["line_count"] == 3

    def test_shell_blocked_writes_meta_and_blocked_status(self):
        events = []
        reg = ToolRegistry(
            archon_source_dir=None,
            confirmer=lambda _command, _level: False,
        )
        reg.set_execute_event_handler(lambda kind, payload: events.append((kind, payload)))

        result = reg.execute("shell", {"command": "echo hi"})

        assert "rejected" in result.lower()
        post_execute = [payload for kind, payload in events if kind == "post_execute"]
        assert len(post_execute) == 1
        assert post_execute[0]["status"] == "blocked"
        assert post_execute[0]["meta"]["blocked"] is True
        assert post_execute[0]["meta"]["command_preview"] == "echo hi"

    def test_edit_file_blocked_writes_meta_and_blocked_status(self, tmp_path: Path):
        file_path = tmp_path / "test.txt"
        file_path.write_text("hello world")
        reg = ToolRegistry(
            archon_source_dir=None,
            confirmer=lambda _command, _level: False,
        )
        events = []
        reg.set_execute_event_handler(lambda kind, payload: events.append((kind, payload)))

        result = reg.execute(
            "edit_file",
            {"path": str(file_path), "old": "hello", "new": "goodbye"},
        )

        assert "rejected" in result.lower()
        post_execute = [payload for kind, payload in events if kind == "post_execute"]
        assert len(post_execute) == 1
        assert post_execute[0]["status"] == "blocked"
        assert post_execute[0]["meta"]["blocked"] is True
        assert post_execute[0]["meta"]["command_preview"] == f"edit_file: {file_path.resolve()}"


def test_slow_non_shell_tool_gets_heartbeat():
    events = []
    reg = ToolRegistry(archon_source_dir=None, confirmer=lambda _command, _level: True)
    reg.set_execute_event_handler(lambda kind, payload: events.append((kind, payload)))

    def slow_tool(seconds: int = 3, _ctx=None) -> str:
        time_mod.sleep(seconds)
        return "done"

    reg.register(
        "slow_tool",
        "test slow tool",
        {
            "properties": {"seconds": {"type": "integer"}},
            "required": [],
        },
        slow_tool,
    )

    result = reg.execute("slow_tool", {"seconds": 3})
    assert result == "done"
    ux_events = [payload for kind, payload in events if kind == "ux_event"]
    heartbeats = [
        payload for payload in ux_events
        if getattr(payload.get("event"), "data", {}).get("detail_type") == "heartbeat"
    ]
    assert len(heartbeats) >= 1


def test_shell_does_not_get_heartbeat():
    events = []
    reg = ToolRegistry(archon_source_dir=None, confirmer=lambda _command, _level: True)
    reg.set_execute_event_handler(lambda kind, payload: events.append((kind, payload)))

    result = reg.execute("shell", {"command": "sleep 3", "timeout": 5})
    assert "[exit_code=0]" in result
    ux_events = [payload for kind, payload in events if kind == "ux_event"]
    heartbeats = [
        payload for payload in ux_events
        if getattr(payload.get("event"), "data", {}).get("detail_type") == "heartbeat"
    ]
    assert heartbeats == []
