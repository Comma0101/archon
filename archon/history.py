"""Conversation history persistence (JSONL sessions)."""

from __future__ import annotations

import json
import time
from pathlib import Path

from archon.config import HISTORY_DIR


def _ensure_dir() -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)


def new_session_id() -> str:
    """Generate a timestamp-based session ID."""
    return time.strftime("%Y%m%d-%H%M%S")


def session_path(session_id: str) -> Path:
    """Return the file path for a session."""
    return HISTORY_DIR / f"{session_id}.jsonl"


def save_message(session_id: str, role: str, content: str) -> None:
    """Append a single message to the session file."""
    _ensure_dir()
    entry = {
        "role": role,
        "content": content,
        "timestamp": time.time(),
    }
    path = session_path(session_id)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def save_exchange(session_id: str, user_msg: str, assistant_msg: str) -> None:
    """Append a user/assistant exchange to the session file."""
    save_message(session_id, "user", user_msg)
    save_message(session_id, "assistant", assistant_msg)


def load_session(session_id: str) -> list[dict]:
    """Load all messages from a session file."""
    path = session_path(session_id)
    if not path.exists():
        return []
    messages = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return messages


def list_sessions(limit: int = 20) -> list[dict]:
    """List recent sessions with metadata (newest first)."""
    _ensure_dir()
    files = sorted(HISTORY_DIR.glob("*.jsonl"), reverse=True)
    sessions = []
    for f in files[:limit]:
        sid = f.stem
        stat = f.stat()
        # Count lines (messages) without loading full content
        with open(f, "r", encoding="utf-8") as fh:
            msg_count = sum(1 for line in fh if line.strip())
        sessions.append({
            "session_id": sid,
            "messages": msg_count,
            "size_bytes": stat.st_size,
            "modified": stat.st_mtime,
        })
    return sessions


def delete_session(session_id: str) -> bool:
    """Delete a session file. Returns True if deleted."""
    path = session_path(session_id)
    if path.exists():
        path.unlink()
        return True
    return False
