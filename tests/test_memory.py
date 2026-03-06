"""Tests for memory indexing and lookup."""

import json

from archon import memory


class TestMemoryIndex:
    def test_rebuild_index_classifies_files_and_lookup_ranks_results(self, monkeypatch, tmp_path):
        monkeypatch.setattr("archon.memory.MEMORY_DIR", tmp_path)

        memory.write(
            "profiles/system.md",
            "# System Hardware Profile\n\nCPU, GPU, RAM, storage layout and mounts.\n",
        )
        memory.write(
            "projects/korami-site.md",
            "# Korami Site Frontend\n\nNext.js frontend for Korami Voice Agent.\n",
        )
        memory.write(
            "decisions/2026-02-24-memory-index.md",
            "# Memory Index Decision\n\nUse a machine-readable index for memory routing.\n",
        )

        payload = memory.rebuild_index()

        idx_path = memory.index_path()
        assert idx_path.exists()
        raw = json.loads(idx_path.read_text())
        assert raw["entries"]
        assert payload["entries"]

        by_path = {e["path"]: e for e in payload["entries"]}
        assert by_path["profiles/system.md"]["kind"] == "system_profile"
        assert by_path["profiles/system.md"]["scope"] == "global"
        assert by_path["projects/korami-site.md"]["kind"] == "project"
        assert by_path["projects/korami-site.md"]["scope"] == "project:korami-site"
        assert by_path["decisions/2026-02-24-memory-index.md"]["kind"] == "decision"

        hits = memory.lookup("gpu storage mounts", limit=3)
        assert hits
        assert hits[0]["path"] == "profiles/system.md"
        assert hits[0]["score"] > 0

    def test_write_auto_updates_memory_index(self, monkeypatch, tmp_path):
        monkeypatch.setattr("archon.memory.MEMORY_DIR", tmp_path)

        memory.write("projects/example.md", "# Example Project\n\nProject summary.\n")

        idx = json.loads(memory.index_path().read_text())
        paths = {entry["path"] for entry in idx["entries"]}
        assert "projects/example.md" in paths

    def test_prefetch_for_query_returns_ranked_snippets(self, monkeypatch, tmp_path):
        monkeypatch.setattr("archon.memory.MEMORY_DIR", tmp_path)

        memory.write("profiles/system.md", "# System Hardware Profile\n\nGPU RAM storage mounts.\n")
        memory.write("projects/korami-site.md", "# Korami Site\n\nFrontend app.\n")

        prefetched = memory.prefetch_for_query("what do you think about my system storage", limit=2)
        assert prefetched
        assert prefetched[0]["path"] == "profiles/system.md"
        assert "System Hardware Profile" in prefetched[0]["excerpt"]
        assert prefetched[0]["score"] > 0
        assert prefetched[0]["stability"] == "semi_stable"
        assert prefetched[0]["confidence"] == "high"
        assert prefetched[0]["last_modified"]

    def test_rebuild_index_tracks_layer_metadata_for_layered_paths(self, monkeypatch, tmp_path):
        monkeypatch.setattr("archon.memory.MEMORY_DIR", tmp_path)

        memory.write("profiles/system.md", "# System Hardware Profile\n\nGPU RAM storage mounts.\n")
        memory.write("projects/korami-site.md", "# Korami Site\n\nFrontend app.\n")
        memory.write("compactions/sessions/test-session.md", "# Session Compaction Summary\n\n- user: We were fixing token bloat.\n")
        memory.write("compactions/tasks/task-123.md", "# Task Compaction Summary\n\n- assistant: Added routing.\n")

        payload = memory.rebuild_index()
        by_path = {entry["path"]: entry for entry in payload["entries"]}

        assert by_path["profiles/system.md"]["layer"] == "machine"
        assert by_path["projects/korami-site.md"]["layer"] == "project"
        assert by_path["compactions/sessions/test-session.md"]["layer"] == "session"
        assert by_path["compactions/tasks/task-123.md"]["layer"] == "task"

    def test_compact_history_writes_session_summary_artifact(self, monkeypatch, tmp_path):
        monkeypatch.setattr("archon.memory.MEMORY_DIR", tmp_path)

        artifact = memory.compact_history(
            [
                {"role": "user", "content": "We need to reduce token bloat in long chats."},
                {"role": "assistant", "content": [{"type": "text", "text": "We should compact old context into memory."}]},
            ],
            layer="session",
            summary_id="sess-1",
        )

        assert artifact["path"] == "compactions/sessions/sess-1.md"
        text = memory.read("compactions/sessions/sess-1.md")
        assert "Session Compaction Summary" in text
        assert "token bloat" in text

        payload = memory.load_index()
        by_path = {entry["path"]: entry for entry in payload["entries"]}
        assert by_path["compactions/sessions/sess-1.md"]["kind"] == "compaction_summary"
        assert by_path["compactions/sessions/sess-1.md"]["layer"] == "session"

    def test_compact_history_writes_task_summary_artifact(self, monkeypatch, tmp_path):
        monkeypatch.setattr("archon.memory.MEMORY_DIR", tmp_path)

        artifact = memory.compact_history(
            [
                {"role": "user", "content": "We narrowed the task to trimming long chat history."},
                {"role": "assistant", "content": [{"type": "text", "text": "Next we should keep only the current task context."}]},
            ],
            layer="task",
            summary_id="task-1",
        )

        assert artifact["path"] == "compactions/tasks/task-1.md"
        text = memory.read("compactions/tasks/task-1.md")
        assert "Task Compaction Summary" in text
        assert "current task context" in text

        payload = memory.load_index()
        by_path = {entry["path"]: entry for entry in payload["entries"]}
        assert by_path["compactions/tasks/task-1.md"]["kind"] == "compaction_summary"
        assert by_path["compactions/tasks/task-1.md"]["layer"] == "task"

    def test_compact_history_preserves_tool_use_blocks(self, monkeypatch, tmp_path):
        monkeypatch.setattr("archon.memory.MEMORY_DIR", tmp_path)

        artifact = memory.compact_history(
            [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "read_file",
                            "input": {"path": "/tmp/demo.txt"},
                        }
                    ],
                }
            ],
            layer="session",
            summary_id="sess-tool-use",
        )

        text = memory.read(artifact["path"])
        assert "tool_use read_file" in text
        assert "/tmp/demo.txt" in text

    def test_compact_history_marks_omitted_messages_when_truncated(self, monkeypatch, tmp_path):
        monkeypatch.setattr("archon.memory.MEMORY_DIR", tmp_path)

        artifact = memory.compact_history(
            [{"role": "user", "content": f"message {idx}"} for idx in range(10)],
            layer="session",
            summary_id="sess-many",
            max_entries=8,
        )

        text = memory.read(artifact["path"])
        assert "Omitted 2 earlier messages" in text
        assert "message 9" in text

    def test_prefetch_for_query_prefers_compaction_summary_when_relevant(self, monkeypatch, tmp_path):
        monkeypatch.setattr("archon.memory.MEMORY_DIR", tmp_path)

        memory.write("projects/korami-site.md", "# Korami Site\n\nFrontend app.\n")
        memory.compact_history(
            [
                {"role": "user", "content": "We need to reduce token bloat in long chats."},
                {"role": "assistant", "content": [{"type": "text", "text": "We should compact old context into memory."}]},
            ],
            layer="session",
            summary_id="sess-2",
        )

        prefetched = memory.prefetch_for_query(
            "what were we doing about token bloat",
            limit=2,
            min_score=0,
        )

        assert prefetched
        assert prefetched[0]["kind"] == "compaction_summary"
        assert prefetched[0]["layer"] == "session"
        assert "token bloat" in prefetched[0]["excerpt"]

    def test_write_canonicalizes_system_profile_and_updates_memory_md_pointer(self, monkeypatch, tmp_path):
        monkeypatch.setattr("archon.memory.MEMORY_DIR", tmp_path)

        memory.write(
            "system-profile.md",
            "# System Hardware Profile\n\nCPU/GPU/RAM/storage baseline.\n",
        )

        assert not (tmp_path / "system-profile.md").exists()
        canonical = tmp_path / "profiles" / "system.md"
        assert canonical.exists()

        memory_md = tmp_path / "MEMORY.md"
        assert memory_md.exists()
        text = memory_md.read_text()
        assert "profiles/system.md" in text
        assert "projects.md" in text

        # Pointer block should be stable/replaced instead of duplicated.
        memory.write(
            "profiles/system.md",
            "# System Hardware Profile\n\nUpdated baseline.\n",
        )
        text2 = memory_md.read_text()
        assert text2.count("profiles/system.md") == 1

    def test_read_system_profile_legacy_path_falls_back_before_migration(self, monkeypatch, tmp_path):
        monkeypatch.setattr("archon.memory.MEMORY_DIR", tmp_path)
        (tmp_path / "system-profile.md").write_text("# Legacy System Profile\n")

        text = memory.read("system-profile.md")
        assert "Legacy System Profile" in text


class TestMemoryInbox:
    def test_inbox_add_list_and_reject(self, monkeypatch, tmp_path):
        monkeypatch.setattr("archon.memory.MEMORY_DIR", tmp_path)

        item = memory.inbox_add(
            kind="preference",
            scope="global",
            summary="User prefers OpenCode for deep code reviews.",
            source="user_message",
        )
        assert item["status"] == "pending"
        assert item["kind"] == "preference"

        pending = memory.inbox_list(status="pending")
        assert len(pending) == 1
        assert pending[0]["id"] == item["id"]

        decided = memory.inbox_decide(item["id"], decision="reject")
        assert decided is not None
        assert decided["status"] == "rejected"

        pending_after = memory.inbox_list(status="pending")
        assert pending_after == []

    def test_inbox_apply_writes_memory_and_marks_applied(self, monkeypatch, tmp_path):
        monkeypatch.setattr("archon.memory.MEMORY_DIR", tmp_path)

        item = memory.inbox_add(
            kind="project_fact",
            scope="project:korami-site",
            summary="Korami-site uses Next.js 15 and React 19.",
            source="worker_session:abc",
            target_path="projects/korami-site.md",
            content="- Stack: Next.js 15, React 19\\n",
        )

        applied = memory.inbox_decide(item["id"], decision="apply")
        assert applied is not None
        assert applied["status"] == "applied"
        assert applied["applied_path"] == "projects/korami-site.md"

        text = memory.read("projects/korami-site.md")
        assert "Next.js 15" in text

    def test_inbox_apply_is_idempotent_and_reject_after_apply_is_invalid(self, monkeypatch, tmp_path):
        monkeypatch.setattr("archon.memory.MEMORY_DIR", tmp_path)

        item = memory.inbox_add(
            kind="project_fact",
            scope="project:korami-site",
            summary="Korami-site uses Next.js 15 and React 19.",
            source="worker_session:abc",
            target_path="projects/korami-site.md",
            content="- Stack: Next.js 15, React 19\\n",
        )

        applied1 = memory.inbox_decide(item["id"], decision="apply")
        assert applied1 is not None
        assert applied1["status"] == "applied"

        applied2 = memory.inbox_decide(item["id"], decision="apply")
        assert applied2 is not None
        assert applied2["status"] == "applied"

        text = memory.read("projects/korami-site.md")
        assert text.count("Next.js 15") == 1

        rejected_after_apply = memory.inbox_decide(item["id"], decision="reject")
        assert rejected_after_apply is None

        inbox_items = memory.inbox_list(status="all")
        assert inbox_items[0]["status"] == "applied"
        assert inbox_items[0]["decision"] == "apply"

    def test_capture_preference_to_inbox_for_explicit_preference(self, monkeypatch, tmp_path):
        monkeypatch.setattr("archon.memory.MEMORY_DIR", tmp_path)

        item = memory.capture_preference_to_inbox(
            "I prefer OpenCode for deep code reviews.",
            source="user_message",
        )
        assert item is not None
        assert item["kind"] == "preference"
        assert item["scope"] == "global"
        assert "OpenCode" in item["summary"]

        pending = memory.inbox_list(status="pending")
        assert len(pending) == 1
        assert pending[0]["id"] == item["id"]

    def test_capture_preference_to_inbox_ignores_non_preference_question(self, monkeypatch, tmp_path):
        monkeypatch.setattr("archon.memory.MEMORY_DIR", tmp_path)

        item = memory.capture_preference_to_inbox(
            "What do you think about my system?",
            source="user_message",
        )
        assert item is None
        assert memory.inbox_list(status="pending") == []

    def test_inbox_apply_replace_section_updates_existing_markdown_section(self, monkeypatch, tmp_path):
        monkeypatch.setattr("archon.memory.MEMORY_DIR", tmp_path)

        memory.write(
            "profiles/system.md",
            "# System Hardware Profile\n\n"
            "## CPU\n"
            "- Old CPU\n\n"
            "## GPU\n"
            "- Old GPU\n",
        )
        item = memory.inbox_add(
            kind="system_fact",
            scope="global",
            summary="Update GPU section",
            source="worker_session:abc",
            target_path="profiles/system.md",
            content="## GPU\n- NVIDIA RTX 4070 SUPER\n",
        )

        applied = memory.inbox_decide(
            item["id"],
            decision="apply",
            apply_mode="replace_section",
            section_heading="## GPU",
        )

        assert applied is not None
        assert applied["status"] == "applied"
        assert applied["apply_mode"] == "replace_section"
        text = memory.read("profiles/system.md")
        assert "- Old GPU" not in text
        assert "NVIDIA RTX 4070 SUPER" in text
        assert "- Old CPU" in text
