from __future__ import annotations

from pathlib import Path

from archon.config import ActivityConfig


def test_activity_status_disabled():
    from archon.cli_activity_commands import activity_status_impl

    config = ActivityConfig(enabled=False)
    output: list[str] = []
    activity_status_impl(
        config=config,
        activity_dir=Path("/tmp/activity"),
        echo_fn=output.append,
    )
    assert any("disabled" in line.lower() for line in output)


def test_activity_summary_uses_non_mutating_scan(tmp_path, monkeypatch):
    from archon.activity import ActivitySummary, PackageSummary
    from archon.cli_activity_commands import activity_summary_impl

    config = ActivityConfig(enabled=True, repo_paths=[str(tmp_path)])
    output: list[str] = []
    calls: list[bool] = []

    summary = ActivitySummary(
        scanned_at=__import__("datetime").datetime(2026, 3, 23, tzinfo=__import__("datetime").timezone.utc),
        since=__import__("datetime").datetime(2026, 3, 22, tzinfo=__import__("datetime").timezone.utc),
        git=[],
        packages=PackageSummary([], [], []),
        working_trees=[],
        system=None,
    )

    def fake_scan_and_store(cfg, activity_dir, *, persist_last_session=True):
        calls.append(persist_last_session)
        return summary

    monkeypatch.setattr("archon.cli_activity_commands.scan_and_store", fake_scan_and_store)
    monkeypatch.setattr(
        "archon.cli_activity_commands.format_activity_report",
        lambda result: "[Recent Activity]",
    )

    activity_summary_impl(
        config=config,
        activity_dir=tmp_path / "activity",
        echo_fn=output.append,
    )

    assert calls == [False]
    assert output == ["[Recent Activity]"]


def test_activity_reset(tmp_path):
    from archon.cli_activity_commands import activity_reset_impl

    activity_dir = tmp_path / "activity"
    snapshots_dir = activity_dir / "snapshots"
    snapshots_dir.mkdir(parents=True)
    (snapshots_dir / "test.jsonl").write_text("{}")
    (activity_dir / "last_session.json").write_text('{"last_scan": "2026-03-23T00:00:00+00:00"}')

    output: list[str] = []
    activity_reset_impl(
        activity_dir=activity_dir,
        echo_fn=output.append,
    )
    assert not (activity_dir / "last_session.json").exists()
    assert not list(snapshots_dir.glob("*.jsonl"))
    assert any("cleared" in line.lower() for line in output)
