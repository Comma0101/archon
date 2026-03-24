"""CLI command implementations for activity context management."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from archon.activity import (
    format_activity_report,
    load_last_session,
    scan_and_store,
)
from archon.config import ActivityConfig


def activity_status_impl(
    *,
    config: ActivityConfig,
    activity_dir: Path,
    echo_fn: Callable[[str], None],
) -> None:
    """Show activity config and last scan time."""
    if not config.enabled:
        echo_fn("Activity context: disabled")
        echo_fn("Enable with [activity] enabled = true in config.toml")
        return

    echo_fn("Activity context: enabled")
    echo_fn(f"  Repos: {', '.join(config.repo_paths) or '(none configured)'}")
    echo_fn(f"  Gap threshold: {config.gap_threshold_minutes}m")
    echo_fn(f"  Token budget: {config.token_budget}")
    echo_fn(f"  Summarizer: {config.summarizer}")

    last_ts = load_last_session(activity_dir)
    if last_ts:
        echo_fn(f"  Last scan: {last_ts.isoformat()}")
    else:
        echo_fn("  Last scan: never")


def activity_summary_impl(
    *,
    config: ActivityConfig,
    activity_dir: Path,
    echo_fn: Callable[[str], None],
) -> None:
    """Run collectors and display full summary without advancing the session cursor."""
    if not config.enabled:
        echo_fn("Activity context: disabled")
        return

    summary = scan_and_store(
        config,
        activity_dir=activity_dir,
        persist_last_session=False,
    )
    if summary is None:
        echo_fn("No activity recorded.")
        return

    text = format_activity_report(summary)
    if text:
        echo_fn(text)
    else:
        echo_fn("No activity detected since last session.")


def activity_reset_impl(
    *,
    activity_dir: Path,
    echo_fn: Callable[[str], None],
) -> None:
    """Delete all snapshots and reset the last-session marker."""
    snapshots_dir = activity_dir / "snapshots"
    count = 0
    if snapshots_dir.exists():
        for path in snapshots_dir.glob("*.jsonl"):
            try:
                path.unlink()
                count += 1
            except OSError:
                continue

    last_session = activity_dir / "last_session.json"
    if last_session.exists():
        try:
            last_session.unlink()
        except OSError:
            pass

    echo_fn(f"Activity data cleared ({count} snapshots removed).")
