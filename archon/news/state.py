"""State management for news briefing idempotency and cached digests."""

from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

from archon.config import NEWS_CACHE_DIR, NEWS_STATE_DIR
from archon.news.models import NewsDigest


def news_state_path(path: Path | None = None) -> Path:
    """Return the news state file path."""
    if path is not None:
        return Path(path)
    return NEWS_STATE_DIR / "state.json"


def news_digest_cache_path(
    date_iso: str | None = None,
    path: Path | None = None,
) -> Path:
    """Return the cached digest artifact path for a date (defaults to today)."""
    if path is not None:
        return Path(path)
    day = date_iso or dt.date.today().isoformat()
    return NEWS_CACHE_DIR / f"digest-{day}.json"


def _default_state() -> dict:
    return {"last_run": None, "status": None, "timestamp": None}


def load_news_state(path: Path | None = None) -> dict:
    """Load news state, returning defaults if the file is missing."""
    state_file = news_state_path(path)
    if not state_file.exists():
        return _default_state()
    with open(state_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    state = _default_state()
    state.update({k: data.get(k) for k in state.keys()})
    return state


def load_cached_digest(
    date_iso: str | None = None,
    path: Path | None = None,
) -> NewsDigest | None:
    """Load a cached digest artifact if present and valid."""
    digest_file = news_digest_cache_path(date_iso=date_iso, path=path)
    if not digest_file.exists():
        return None
    try:
        with open(digest_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    digest = NewsDigest.from_dict(data)
    if not digest.date_iso or not digest.markdown:
        return None
    return digest


def save_news_state(
    status: str,
    path: Path | None = None,
    now: dt.datetime | None = None,
) -> dict:
    """Persist current run state and return the saved payload."""
    now_dt = now or dt.datetime.now()
    payload = {
        "last_run": now_dt.date().isoformat(),
        "status": status,
        "timestamp": now_dt.timestamp() if now else time.time(),
    }
    state_file = news_state_path(path)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    return payload


def save_cached_digest(
    digest: NewsDigest,
    path: Path | None = None,
    now: dt.datetime | None = None,
) -> dict:
    """Persist a rendered digest artifact for cache reuse."""
    payload = digest.to_dict()
    payload["cached_at"] = (now or dt.datetime.now()).timestamp()
    digest_file = news_digest_cache_path(date_iso=digest.date_iso, path=path)
    digest_file.parent.mkdir(parents=True, exist_ok=True)
    with open(digest_file, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    return payload


def should_run_today(
    force: bool = False,
    run_after_hour_local: int = 8,
    state: dict | None = None,
    now: dt.datetime | None = None,
) -> tuple[bool, str | None]:
    """Return whether a daily news run should execute and an optional skip reason."""
    if force:
        return True, None

    now_dt = now or dt.datetime.now()
    current_state = state or load_news_state()
    today = now_dt.date().isoformat()
    if current_state.get("last_run") == today:
        return False, "already_ran_today"

    if now_dt.hour < int(run_after_hour_local):
        return False, "before_run_window"

    return True, None
