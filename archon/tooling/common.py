"""Shared helpers for local tool formatting and safe self-modification prep."""

import subprocess


def truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n... (truncated, {len(text) - max_chars} chars omitted)"


def auto_commit(source_dir: str) -> None:
    """Auto-commit current state before self-modification (best effort)."""
    try:
        subprocess.run(
            ["git", "-C", source_dir, "stash", "push", "-m", "archon: auto-save before self-modification"],
            capture_output=True,
            timeout=10,
        )
    except Exception:
        pass
