"""Tests for shared tool-result history shaping helpers."""

from archon.execution.history_shaping import shape_tool_result_for_history


def test_shell_history_shaping_includes_command_exit_code_and_excerpt():
    result = shape_tool_result_for_history(
        "shell",
        {"command": "printf secret"},
        "\n".join(f"line{index}" for index in range(1, 14)) + "\n[exit_code=0]",
        tool_result_max_chars=6000,
        tool_result_worker_max_chars=2500,
    )

    assert "command: printf secret" in result
    assert "exit_code: 0" in result
    assert "output:" in result
    assert "line1" in result
    assert "... [3 lines omitted] ..." in result


def test_read_file_history_shaping_keeps_path_offset_limit_and_excerpt():
    result = shape_tool_result_for_history(
        "read_file",
        {"path": "/tmp/example.py", "offset": 12, "limit": 24},
        "\n".join(f"line {index}" for index in range(1, 15)),
        tool_result_max_chars=6000,
        tool_result_worker_max_chars=2500,
    )

    assert "path: /tmp/example.py" in result
    assert "offset: 12" in result
    assert "limit: 24" in result
    assert "excerpt:" in result
    assert "line 1" in result
    assert "... [2 lines omitted] ..." in result


def test_sampled_tools_include_counts_and_samples():
    result = shape_tool_result_for_history(
        "grep",
        {"root": "/repo", "pattern": "needle", "glob": "*.py"},
        "a\nb\nc\nd\ne\nf\ng\nh\ni",
        tool_result_max_chars=6000,
        tool_result_worker_max_chars=2500,
    )

    assert "root: /repo" in result
    assert "pattern: needle" in result
    assert "glob: *.py" in result
    assert "matches: 9" in result
    assert "sample:" in result
    assert "a" in result


def test_generic_truncation_applies_for_unhandled_tools():
    result = shape_tool_result_for_history(
        "other_tool",
        {},
        "x" * 100,
        tool_result_max_chars=20,
        tool_result_worker_max_chars=10,
    )

    assert len(result) < 100
    assert "[80 chars omitted]" in result
