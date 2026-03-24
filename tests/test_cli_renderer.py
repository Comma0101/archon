"""Tests for CLI surface renderer."""

from archon.ux import events
from archon.ux.cli_renderer import CLIRenderer


class _Buffer:
    def __init__(self):
        self.parts = []

    def write(self, text: str) -> None:
        self.parts.append(text)

    def flush(self) -> None:
        pass

    def render(self) -> str:
        return "".join(self.parts)


def test_render_tool_end_completed():
    buf = _Buffer()
    renderer = CLIRenderer(write_fn=buf.write, flush_fn=buf.flush)
    evt = events.tool_end("shell", "shell: exit 0 (5 lines)", session_id="s1")
    renderer.render_event(evt, status="completed")
    output = buf.render()
    assert "✓" in output
    assert "shell: exit 0 (5 lines)" in output


def test_render_tool_end_failed():
    buf = _Buffer()
    renderer = CLIRenderer(write_fn=buf.write, flush_fn=buf.flush)
    evt = events.tool_end("shell", "shell: exit 1 (error)", session_id="s1")
    renderer.render_event(evt, status="failed")
    output = buf.render()
    assert "✗" in output
    assert "shell: exit 1 (error)" in output


def test_render_tool_blocked():
    buf = _Buffer()
    renderer = CLIRenderer(write_fn=buf.write, flush_fn=buf.flush)
    evt = events.tool_blocked(
        tool="shell",
        session_id="s1",
        command_preview="pacman -Syu",
        safety_level="DANGEROUS",
    )
    renderer.render_event(evt)
    output = buf.render()
    assert "⚠" in output
    assert "pacman -Syu" in output
    assert "/approve" in output


def test_render_tool_running_output_line():
    buf = _Buffer()
    renderer = CLIRenderer(write_fn=buf.write, flush_fn=buf.flush)
    evt = events.tool_running(
        tool="shell",
        session_id="s1",
        detail_type="output_line",
        line="==> Building...",
    )
    renderer.render_event(evt)
    output = buf.render()
    assert "│" in output
    assert "==> Building..." in output


def test_render_tool_diff():
    buf = _Buffer()
    renderer = CLIRenderer(write_fn=buf.write, flush_fn=buf.flush)
    evt = events.tool_diff(
        tool="edit_file",
        session_id="s1",
        path="foo.py",
        diff_lines=["-old", "+new"],
        lines_changed=1,
    )
    renderer.render_event(evt)
    output = buf.render()
    assert "-old" in output
    assert "+new" in output
