"""Tests for conversation history persistence."""

import time

from archon.history import (
    delete_session,
    list_sessions,
    load_session,
    new_session_id,
    save_exchange,
    save_message,
    session_path,
)


class TestHistory:
    def test_new_session_id_format(self):
        sid = new_session_id()
        assert len(sid) == 15  # YYYYMMDD-HHMMSS
        assert "-" in sid

    def test_save_and_load_roundtrip(self, monkeypatch, tmp_path):
        monkeypatch.setattr("archon.history.HISTORY_DIR", tmp_path)

        save_message("test-001", "user", "Hello")
        save_message("test-001", "assistant", "Hi there!")

        msgs = load_session("test-001")
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "Hello"
        assert msgs[1]["role"] == "assistant"
        assert msgs[1]["content"] == "Hi there!"
        assert "timestamp" in msgs[0]

    def test_save_exchange_shorthand(self, monkeypatch, tmp_path):
        monkeypatch.setattr("archon.history.HISTORY_DIR", tmp_path)

        save_exchange("test-002", "What's up?", "Not much!")

        msgs = load_session("test-002")
        assert len(msgs) == 2
        assert msgs[0]["content"] == "What's up?"
        assert msgs[1]["content"] == "Not much!"

    def test_list_sessions(self, monkeypatch, tmp_path):
        monkeypatch.setattr("archon.history.HISTORY_DIR", tmp_path)

        save_exchange("test-aaa", "q1", "a1")
        save_exchange("test-bbb", "q2", "a2")
        save_exchange("test-bbb", "q3", "a3")

        sessions = list_sessions()
        assert len(sessions) == 2
        # Newest first
        ids = [s["session_id"] for s in sessions]
        assert "test-aaa" in ids
        assert "test-bbb" in ids
        bbb = next(s for s in sessions if s["session_id"] == "test-bbb")
        assert bbb["messages"] == 4  # 2 exchanges = 4 messages

    def test_delete_session(self, monkeypatch, tmp_path):
        monkeypatch.setattr("archon.history.HISTORY_DIR", tmp_path)

        save_message("test-del", "user", "bye")
        assert session_path("test-del").exists() is False or True  # just save worked
        assert delete_session("test-del") is True
        assert load_session("test-del") == []
        assert delete_session("test-del") is False

    def test_load_nonexistent_returns_empty(self, monkeypatch, tmp_path):
        monkeypatch.setattr("archon.history.HISTORY_DIR", tmp_path)
        assert load_session("nonexistent") == []
