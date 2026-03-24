"""Tests for shared rendering logic."""

from archon.ux.renderers import build_tool_summary, collapse_output_lines, truncate_diff_lines


class TestBuildToolSummary:
    def test_shell_from_meta(self):
        meta = {"exit_code": 0, "line_count": 14}
        assert build_tool_summary("shell", meta, "") == "shell: exit 0 (14 lines)"

    def test_shell_error_from_meta(self):
        meta = {"exit_code": 1, "line_count": 3}
        assert build_tool_summary("shell", meta, "") == "shell: exit 1 (3 lines)"

    def test_read_file_from_meta(self):
        meta = {"path": "/etc/pacman.conf", "line_count": 74}
        assert build_tool_summary("read_file", meta, "") == "read: /etc/pacman.conf (74 lines)"

    def test_edit_file_from_meta(self):
        meta = {"path": "agent.py", "line_number": 42, "lines_changed": 1}
        assert build_tool_summary("edit_file", meta, "") == "edit: agent.py:42 (1 line changed)"

    def test_write_file_new_from_meta(self):
        meta = {"path": "config.py", "line_count": 38, "is_new": True}
        assert build_tool_summary("write_file", meta, "") == "write: config.py (new, 38 lines)"

    def test_write_file_existing_from_meta(self):
        meta = {"path": "config.py", "line_count": 38, "is_new": False}
        assert build_tool_summary("write_file", meta, "") == "write: config.py (38 lines)"

    def test_grep_from_meta(self):
        meta = {"pattern": "max_iter", "match_count": 3, "file_count": 2}
        assert build_tool_summary("grep", meta, "") == "grep: 'max_iter' -> 3 matches in 2 files"

    def test_glob_from_meta(self):
        meta = {"pattern": "*.py", "file_count": 47}
        assert build_tool_summary("glob", meta, "") == "glob: *.py -> 47 files"

    def test_fallback_unknown_tool_empty_meta(self):
        assert build_tool_summary("web_search", {}, "some result") == "web_search: done"

    def test_fallback_parse_shell_exit_code(self):
        result_str = "hello world\n[exit_code=0]"
        assert build_tool_summary("shell", {}, result_str) == "shell: exit 0 (1 line)"


class TestCollapseOutputLines:
    def test_short_output_unchanged(self):
        lines = [f"line {i}" for i in range(5)]
        assert collapse_output_lines(lines, max_lines=20) == lines

    def test_long_output_collapsed(self):
        lines = [f"line {i}" for i in range(30)]
        result = collapse_output_lines(lines, max_lines=20)
        assert len(result) == 14
        assert result[0] == "line 0"
        assert result[7] == "line 7"
        assert result[8] == "... (17 more lines)"
        assert result[-1] == "line 29"


class TestTruncateDiffLines:
    def test_short_diff_unchanged(self):
        lines = ["-old", "+new"]
        assert truncate_diff_lines(lines, max_lines=10) == lines

    def test_long_diff_truncated(self):
        lines = [f"-line {i}" for i in range(15)]
        result = truncate_diff_lines(lines, max_lines=10)
        assert len(result) == 11
        assert result[-1] == "... (5 more lines changed)"
