"""Tests for edit_file diff generation via _ctx."""

from pathlib import Path

from archon.tools import ToolRegistry


def test_edit_emits_diff_event(tmp_path):
    file_path = tmp_path / "sample.py"
    file_path.write_text("def run():\n    max_iter = 15\n    return max_iter\n")

    events = []
    reg = ToolRegistry(archon_source_dir=None, confirmer=lambda _command, _level: True)
    reg.set_execute_event_handler(lambda kind, payload: events.append((kind, payload)))

    result = reg.execute(
        "edit_file",
        {
            "path": str(file_path),
            "old": "max_iter = 15",
            "new": "max_iter = 30",
        },
    )

    assert "Edited" in result
    ux_events = [payload for kind, payload in events if kind == "ux_event"]
    diff_events = [
        payload for payload in ux_events
        if getattr(payload.get("event"), "kind", "") == "tool_diff"
    ]
    assert len(diff_events) == 1
    diff_data = diff_events[0]["event"].data
    assert diff_data["path"] == str(file_path.resolve())
    assert any(line.startswith("-") and "15" in line for line in diff_data["diff_lines"])
    assert any(line.startswith("+") and "30" in line for line in diff_data["diff_lines"])


def test_edit_no_diff_on_failure(tmp_path):
    file_path = tmp_path / "sample.py"
    file_path.write_text("hello world\n")

    events = []
    reg = ToolRegistry(archon_source_dir=None, confirmer=lambda _command, _level: True)
    reg.set_execute_event_handler(lambda kind, payload: events.append((kind, payload)))

    result = reg.execute(
        "edit_file",
        {
            "path": str(file_path),
            "old": "does not exist",
            "new": "replacement",
        },
    )

    assert "not found" in result
    ux_events = [payload for kind, payload in events if kind == "ux_event"]
    diff_events = [
        payload for payload in ux_events
        if getattr(payload.get("event"), "kind", "") == "tool_diff"
    ]
    assert diff_events == []


def test_edit_skips_diff_for_large_files(tmp_path):
    file_path = tmp_path / "large.py"
    file_path.write_text("x" * 60_000 + "\nfind_me = 1\n")

    events = []
    reg = ToolRegistry(archon_source_dir=None, confirmer=lambda _command, _level: True)
    reg.set_execute_event_handler(lambda kind, payload: events.append((kind, payload)))

    result = reg.execute(
        "edit_file",
        {
            "path": str(file_path),
            "old": "find_me = 1",
            "new": "find_me = 2",
        },
    )

    assert "Edited" in result
    ux_events = [payload for kind, payload in events if kind == "ux_event"]
    diff_events = [
        payload for payload in ux_events
        if getattr(payload.get("event"), "kind", "") == "tool_diff"
    ]
    assert diff_events == []
