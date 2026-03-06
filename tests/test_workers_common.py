"""Tests for shared worker adapter helper utilities."""

from archon.workers.common import (
    first_nonempty_line,
    summarize_cli_run,
    truncate_inline,
    truncate_report,
)


def test_first_nonempty_line_returns_first_stripped_line():
    assert first_nonempty_line("", "\n  \n  hello  \nworld", "fallback") == "hello"


def test_truncate_inline_uses_ellipsis_suffix():
    assert truncate_inline("abcdef", 4) == "abcd..."
    assert truncate_inline("abc", 4) == "abc"


def test_truncate_report_includes_omitted_count():
    out = truncate_report("abcdef", 4)
    assert out.startswith("abcd")
    assert "truncated" in out
    assert "2 chars omitted" in out


def test_summarize_cli_run_formats_success_and_failure():
    assert summarize_cli_run("Codex", "ok", 0, "Line one\nLine two", "") == "Line one"
    assert summarize_cli_run("Codex", "ok", 0, "", "") == "Delegated Codex task completed."
    fail = summarize_cli_run("Codex", "failed", 7, "", "boom happened\nmore")
    assert fail.startswith("Codex exited 7: boom happened")
