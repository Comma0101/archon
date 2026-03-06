"""Tests for news state/idempotency helpers."""

import datetime as dt

from archon.news.models import NewsDigest
from archon.news.state import (
    load_cached_digest,
    load_news_state,
    save_cached_digest,
    save_news_state,
    should_run_today,
)


class TestNewsState:
    def test_load_missing_returns_defaults(self, tmp_path):
        state = load_news_state(tmp_path / "missing.json")
        assert state == {"last_run": None, "status": None, "timestamp": None}

    def test_save_and_load_roundtrip(self, tmp_path):
        path = tmp_path / "news" / "state.json"
        now = dt.datetime(2026, 2, 24, 9, 15, 0)
        saved = save_news_state("success", path=path, now=now)
        loaded = load_news_state(path)

        assert saved["last_run"] == "2026-02-24"
        assert saved["status"] == "success"
        assert loaded["last_run"] == "2026-02-24"
        assert loaded["status"] == "success"
        assert isinstance(loaded["timestamp"], float)

    def test_should_run_before_window(self):
        ok, reason = should_run_today(
            force=False,
            run_after_hour_local=8,
            state={"last_run": None, "status": None, "timestamp": None},
            now=dt.datetime(2026, 2, 24, 7, 59, 0),
        )
        assert ok is False
        assert reason == "before_run_window"

    def test_should_not_run_twice_same_day(self):
        ok, reason = should_run_today(
            force=False,
            run_after_hour_local=8,
            state={"last_run": "2026-02-24", "status": "success", "timestamp": 0.0},
            now=dt.datetime(2026, 2, 24, 12, 0, 0),
        )
        assert ok is False
        assert reason == "already_ran_today"

    def test_should_run_after_window(self):
        ok, reason = should_run_today(
            force=False,
            run_after_hour_local=8,
            state={"last_run": "2026-02-23", "status": "success", "timestamp": 0.0},
            now=dt.datetime(2026, 2, 24, 8, 0, 0),
        )
        assert ok is True
        assert reason is None

    def test_force_overrides_gate(self):
        ok, reason = should_run_today(
            force=True,
            run_after_hour_local=23,
            state={"last_run": "2026-02-24", "status": "success", "timestamp": 0.0},
            now=dt.datetime(2026, 2, 24, 1, 0, 0),
        )
        assert ok is True
        assert reason is None

    def test_save_and_load_cached_digest_roundtrip(self, tmp_path):
        path = tmp_path / "news" / "digest-2026-02-24.json"
        digest = NewsDigest(
            date_iso="2026-02-24",
            markdown="Digest markdown",
            used_fallback=False,
            item_count=2,
            items=[],
        )

        save_cached_digest(digest, path=path, now=dt.datetime(2026, 2, 24, 9, 0, 0))
        loaded = load_cached_digest(path=path)

        assert loaded is not None
        assert loaded.date_iso == "2026-02-24"
        assert loaded.markdown == "Digest markdown"
        assert loaded.item_count == 2
