"""Tests for agent loop detection."""

from archon.agent import _detect_tool_loop


def test_detect_loop_same_tool_3_times():
    """Detect when same tool+args pattern repeats 3+ times."""
    recent_calls = [
        ("shell", {"command": "cat /etc/hosts"}),
        ("shell", {"command": "cat /etc/hosts"}),
        ("shell", {"command": "cat /etc/hosts"}),
    ]
    assert _detect_tool_loop(recent_calls) is True


def test_no_loop_different_tools():
    recent_calls = [
        ("shell", {"command": "ls"}),
        ("read_file", {"path": "/etc/hosts"}),
        ("shell", {"command": "cat /etc/hosts"}),
    ]
    assert _detect_tool_loop(recent_calls) is False


def test_no_loop_few_calls():
    recent_calls = [
        ("shell", {"command": "cat /etc/hosts"}),
        ("shell", {"command": "cat /etc/hosts"}),
    ]
    assert _detect_tool_loop(recent_calls) is False


def test_detect_loop_alternating_pattern():
    """Detect A-B-A-B-A-B repeating patterns."""
    recent_calls = [
        ("shell", {"command": "ls"}),
        ("read_file", {"path": "/tmp/x"}),
        ("shell", {"command": "ls"}),
        ("read_file", {"path": "/tmp/x"}),
        ("shell", {"command": "ls"}),
        ("read_file", {"path": "/tmp/x"}),
    ]
    assert _detect_tool_loop(recent_calls) is True
