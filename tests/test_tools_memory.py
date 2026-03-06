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

class TestMemoryLookupTool:
    def test_memory_lookup_uses_index(self, monkeypatch, tmp_path):
        from archon import memory as memory_store

        monkeypatch.setattr("archon.memory.MEMORY_DIR", tmp_path)

        reg = make_registry()
        reg.execute(
            "memory_write",
            {
                "path": "profiles/system.md",
                "content": "# System Hardware Profile\n\nGPU storage mounts and RAM.\n",
            },
        )
        reg.execute(
            "memory_write",
            {
                "path": "projects/korami-site.md",
                "content": "# Korami Site\n\nFrontend site project.\n",
            },
        )

        result = reg.execute("memory_lookup", {"query": "gpu storage", "limit": 3})
        assert "Memory matches:" in result
        assert "profiles/system.md" in result
        assert "kind: system_profile" in result

    def test_memory_write_reports_canonicalized_system_profile_path(self, monkeypatch, tmp_path):
        monkeypatch.setattr("archon.memory.MEMORY_DIR", tmp_path)
        reg = make_registry()

        result = reg.execute(
            "memory_write",
            {
                "path": "system-profile.md",
                "content": "# System Hardware Profile\n\nSpecs.\n",
            },
        )

        assert "profiles/system.md" in result
        assert "canonicalized" in result

    def test_memory_inbox_tools_queue_and_apply(self, monkeypatch, tmp_path):
        monkeypatch.setattr("archon.memory.MEMORY_DIR", tmp_path)
        reg = make_registry()

        add_result = reg.execute(
            "memory_inbox_add",
            {
                "kind": "project_fact",
                "scope": "project:korami-site",
                "summary": "Korami-site uses Next.js 15.",
                "target_path": "projects/korami-site.md",
                "content": "- Stack: Next.js 15\\n",
                "source": "worker_session:test",
            },
        )
        assert "memory_inbox_id:" in add_result
        inbox_id = add_result.split("memory_inbox_id:", 1)[1].splitlines()[0].strip()

        list_result = reg.execute("memory_inbox_list", {})
        assert inbox_id in list_result
        assert "pending" in list_result

        apply_result = reg.execute(
            "memory_inbox_decide",
            {"inbox_id": inbox_id, "decision": "apply"},
        )
        assert "status: applied" in apply_result
        assert "applied_path: projects/korami-site.md" in apply_result

        mem_text = reg.execute("memory_read", {"path": "projects/korami-site.md"})
        assert "Next.js 15" in mem_text

    def test_memory_inbox_decide_forwards_apply_mode_and_section_heading(self, monkeypatch):
        reg = make_registry()
        calls = {}

        def fake_inbox_decide(**kwargs):
            calls.update(kwargs)
            return {
                "id": kwargs["inbox_id"],
                "status": "applied",
                "decision": "apply",
                "apply_mode": kwargs.get("apply_mode", ""),
                "section_heading": kwargs.get("section_heading", ""),
                "target_path": "profiles/system.md",
                "applied_path": "profiles/system.md",
                "summary": "Updated GPU section",
            }

        monkeypatch.setattr("archon.tooling.memory_tools.memory_store.inbox_decide", fake_inbox_decide)

        out = reg.execute(
            "memory_inbox_decide",
            {
                "inbox_id": "abc",
                "decision": "apply",
                "apply_mode": "replace_section",
                "section_heading": "## GPU",
            },
        )

        assert calls["apply_mode"] == "replace_section"
        assert calls["section_heading"] == "## GPU"
        assert "apply_mode: replace_section" in out
        assert "section_heading: ## GPU" in out

    def test_memory_read_list_ignores_stale_module_memory_dir(self, monkeypatch, tmp_path):
        reg = make_registry()
        live_dir = tmp_path / "live_memory"
        stale_dir = tmp_path / "stale_memory"
        live_dir.mkdir(parents=True, exist_ok=True)
        (live_dir / "note.md").write_text("hello")

        monkeypatch.setattr("archon.memory.MEMORY_DIR", live_dir)
        monkeypatch.setattr(
            "archon.tooling.memory_tools.MEMORY_DIR",
            stale_dir,
            raising=False,
        )

        out = reg.execute("memory_read", {})
        assert "Memory files:" in out
        assert "note.md" in out

    def test_edit_old_not_found(self, tmp_path):
        reg = make_registry_allow_all()
        path = tmp_path / "archon_test_edit2.txt"
        path.write_text("hello world")
        result = reg.execute("edit_file", {
            "path": str(path), "old": "xyz", "new": "abc"
        })
        assert "not found" in result.lower()
