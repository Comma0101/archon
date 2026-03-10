"""Tests for tool registry and built-in tools."""

import os
import tempfile
from pathlib import Path

from archon.config import Config
from archon.news.models import NewsDigest
from archon.news.models import NewsRunResult
from archon.tools import ToolRegistry
from archon.workers.base import WorkerEvent, WorkerResult, WorkerTask
from archon.workers.runtime import ActiveWorkerRun
from archon.workers.session_store import WorkerSessionRecord


def make_registry():
    return ToolRegistry(archon_source_dir=None)


def make_registry_allow_all():
    return ToolRegistry(
        archon_source_dir=None,
        confirmer=lambda _command, _level: True,
    )

class TestRegistry:
    def test_schemas_has_36_tools(self):
        reg = make_registry()
        assert len(reg.get_schemas()) == 36

    def test_schema_names(self):
        reg = make_registry()
        names = {t["name"] for t in reg.get_schemas()}
        assert names == {"shell", "read_file", "write_file", "edit_file",
                        "list_dir", "glob", "grep", "memory_read", "memory_write", "memory_lookup",
                        "memory_inbox_add", "memory_inbox_list", "memory_inbox_decide", "news_brief",
                        "deep_research", "check_research_job", "list_research_jobs",
                        "mcp_call",
                        "voice_service_status", "voice_service_start", "voice_service_stop",
                        "call_mission_start", "call_mission_status", "call_mission_list", "call_mission_cancel",
                        "web_search", "web_read", "delegate_code_task",
                        "worker_status", "worker_list", "worker_start",
                        "worker_send", "worker_poll", "worker_cancel", "worker_approve",
                        "worker_reconcile"}

    def test_unknown_tool(self):
        reg = make_registry()
        result = reg.execute("nonexistent", {})
        assert "Unknown tool" in result

class TestReadFile:
    def test_read_existing(self):
        reg = make_registry()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("line1\nline2\nline3\n")
            path = f.name
        try:
            result = reg.execute("read_file", {"path": path})
            assert "line1" in result
            assert "line2" in result
            assert "line3" in result
        finally:
            os.unlink(path)

    def test_read_nonexistent(self):
        reg = make_registry()
        result = reg.execute("read_file", {"path": "/tmp/nonexistent_file_xyz"})
        assert "not found" in result.lower()

    def test_read_with_offset(self):
        reg = make_registry()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("a\nb\nc\nd\ne\n")
            path = f.name
        try:
            result = reg.execute("read_file", {"path": path, "offset": 2, "limit": 2})
            assert "c" in result
            assert "d" in result
        finally:
            os.unlink(path)

class TestWriteFile:
    def test_write_new_file(self, tmp_path):
        reg = make_registry_allow_all()
        path = tmp_path / "archon_test_write.txt"
        result = reg.execute("write_file", {"path": str(path), "content": "hello"})
        assert "Wrote" in result
        assert path.read_text() == "hello"

    def test_write_creates_parents(self, tmp_path):
        reg = make_registry_allow_all()
        path = tmp_path / "archon_test" / "sub" / "file.txt"
        result = reg.execute("write_file", {"path": str(path), "content": "nested"})
        assert "Wrote" in result
        assert path.read_text() == "nested"

class TestEditFile:
    def test_edit_replaces(self, tmp_path):
        reg = make_registry_allow_all()
        path = tmp_path / "archon_test_edit.txt"
        path.write_text("hello world")
        result = reg.execute("edit_file", {
            "path": str(path), "old": "hello", "new": "goodbye"
        })
        assert "Edited" in result
        assert path.read_text() == "goodbye world"

class TestListDir:
    def test_list_tmp(self):
        reg = make_registry()
        result = reg.execute("list_dir", {"path": "/tmp"})
        assert result  # tmp should have something

    def test_list_nonexistent(self):
        reg = make_registry()
        result = reg.execute("list_dir", {"path": "/tmp/nonexistent_dir_xyz"})
        assert "not found" in result.lower()


class TestGlob:
    def test_glob_matches_files_under_root(self, tmp_path):
        reg = make_registry_allow_all()
        src = tmp_path / "src"
        src.mkdir()
        (src / "one.py").write_text("print('one')\n")
        (src / "two.txt").write_text("two\n")
        nested = src / "nested"
        nested.mkdir()
        (nested / "three.py").write_text("print('three')\n")

        result = reg.execute(
            "glob",
            {"pattern": "**/*.py", "root": str(src)},
        )

        assert str(src / "one.py") in result
        assert str(nested / "three.py") in result
        assert str(src / "two.txt") not in result


class TestGrep:
    def test_grep_matches_lines_under_root(self, tmp_path):
        reg = make_registry_allow_all()
        src = tmp_path / "src"
        src.mkdir()
        (src / "one.py").write_text("alpha\nbeta\n")
        (src / "two.txt").write_text("beta\ngamma\n")

        result = reg.execute(
            "grep",
            {"pattern": "beta", "root": str(src)},
        )

        assert f"{src / 'one.py'}:2:beta" in result
        assert f"{src / 'two.txt'}:1:beta" in result

    def test_grep_respects_filename_glob_filter(self, tmp_path):
        reg = make_registry_allow_all()
        src = tmp_path / "src"
        src.mkdir()
        (src / "one.py").write_text("beta\n")
        (src / "two.txt").write_text("beta\n")

        result = reg.execute(
            "grep",
            {"pattern": "beta", "root": str(src), "glob": "*.py"},
        )

        assert f"{src / 'one.py'}:1:beta" in result
        assert str(src / "two.txt") not in result

class TestShell:
    def test_safe_command(self):
        reg = make_registry()
        result = reg.execute("shell", {"command": "echo hello"})
        assert "hello" in result

    def test_pwd(self):
        reg = make_registry()
        result = reg.execute("shell", {"command": "pwd"})
        assert "/" in result

    def test_custom_confirmer_is_used(self):
        calls = []

        def deny_all(command, level):
            calls.append((command, level))
            return False

        reg = ToolRegistry(archon_source_dir=None, confirmer=deny_all)
        result = reg.execute("shell", {"command": "echo hello"})
        assert "rejected by safety gate" in result.lower()
        assert calls
