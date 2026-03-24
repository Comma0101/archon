"""Tests for shell tool Popen streaming."""

from archon.tools import ToolRegistry


def test_shell_streams_output_lines():
    reg = ToolRegistry(archon_source_dir=None, confirmer=lambda _command, _level: True)
    events = []
    reg.set_execute_event_handler(lambda kind, payload: events.append((kind, payload)))

    result = reg.execute("shell", {"command": "echo line1 && echo line2 && echo line3"})

    assert "[exit_code=0]" in result
    assert "line1" in result
    assert "line2" in result
    assert "line3" in result
    ux_events = [payload for kind, payload in events if kind == "ux_event"]
    line_events = [
        payload for payload in ux_events
        if getattr(payload.get("event"), "data", {}).get("detail_type") == "output_line"
    ]
    assert len(line_events) >= 3


def test_shell_streaming_preserves_exit_code():
    reg = ToolRegistry(archon_source_dir=None, confirmer=lambda _command, _level: True)
    result = reg.execute("shell", {"command": "echo ok && exit 42"})
    assert "[exit_code=42]" in result


def test_shell_streaming_handles_no_output():
    reg = ToolRegistry(archon_source_dir=None, confirmer=lambda _command, _level: True)
    result = reg.execute("shell", {"command": "true"})
    assert "(no output)" in result
    assert "[exit_code=0]" in result


def test_shell_streaming_timeout():
    reg = ToolRegistry(archon_source_dir=None, confirmer=lambda _command, _level: True)
    result = reg.execute("shell", {"command": "sleep 10", "timeout": 1})
    assert "timed out" in result.lower()


def test_shell_streaming_stderr_merged():
    reg = ToolRegistry(archon_source_dir=None, confirmer=lambda _command, _level: True)
    result = reg.execute("shell", {"command": "echo out && echo err >&2 && echo out2"})
    assert "[exit_code=0]" in result
    assert "out" in result
    assert "err" in result
