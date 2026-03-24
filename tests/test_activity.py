from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import subprocess
from unittest.mock import MagicMock, patch

from archon.config import ActivityConfig, Config


def test_activity_config_defaults():
    cfg = ActivityConfig()
    assert cfg.enabled is False
    assert cfg.repo_paths == []
    assert cfg.gap_threshold_minutes == 60
    assert cfg.token_budget == 200
    assert cfg.retention_days == 30
    assert cfg.summarizer == "code"
    assert cfg.max_repos == 5
    assert cfg.max_commits_per_repo == 50


def test_activity_config_on_main_config():
    cfg = Config()
    assert isinstance(cfg.activity, ActivityConfig)
    assert cfg.activity.enabled is False


def test_git_event_creation():
    from archon.activity import GitEvent

    event = GitEvent(
        repo_path=Path("/tmp/repo"),
        commit_hash="abc123",
        timestamp=datetime(2026, 3, 23, tzinfo=timezone.utc),
        subject="fix: something",
        changed_files=["archon/agent.py"],
    )
    assert event.repo_path == Path("/tmp/repo")
    assert event.subject == "fix: something"


def test_package_event_creation():
    from archon.activity import PackageEvent

    event = PackageEvent(
        action="installed",
        package="python-httpx",
        version="0.27.0",
        timestamp=datetime(2026, 3, 23, tzinfo=timezone.utc),
    )
    assert event.action == "installed"
    assert event.package == "python-httpx"


def test_working_tree_summary_creation():
    from archon.activity import WorkingTreeSummary

    wt = WorkingTreeSummary(
        repo_path=Path("/tmp/repo"),
        branch="master",
        dirty=True,
        untracked=1,
        modified=3,
        staged=0,
        stash_count=2,
    )
    assert wt.dirty is True
    assert wt.modified == 3


def test_system_snapshot_creation():
    from archon.activity import SystemSnapshot

    snap = SystemSnapshot(
        uptime_seconds=259200.0,
        load_1=0.4,
        load_5=0.3,
        load_15=0.2,
        mem_used_gb=8.2,
        mem_total_gb=32.0,
        disk_used_gb=120.0,
        disk_total_gb=500.0,
    )
    assert snap.load_1 == 0.4


def test_activity_summary_empty():
    from archon.activity import ActivitySummary, PackageSummary

    summary = ActivitySummary(
        scanned_at=datetime(2026, 3, 23, tzinfo=timezone.utc),
        since=datetime(2026, 3, 22, tzinfo=timezone.utc),
        git=[],
        packages=PackageSummary(installed=[], removed=[], upgraded=[]),
        working_trees=[],
        system=None,
    )
    assert summary.git == []
    assert summary.system is None


def test_collect_git_activity_parses_log(tmp_path):
    from archon.activity import collect_git_activity

    log_output = (
        "abc123|2026-03-23T10:00:00+00:00|feat: add feature\n"
        "archon/agent.py\n"
        "archon/tools.py\n"
        "\n"
        "def456|2026-03-23T09:00:00+00:00|fix: bug\n"
        "tests/test_agent.py\n"
        "\n"
    )
    mock_result = MagicMock()
    mock_result.stdout = log_output
    mock_result.returncode = 0

    with patch("archon.activity.subprocess.run", return_value=mock_result):
        events = collect_git_activity(
            repo_paths=[tmp_path],
            since=datetime(2026, 3, 22, tzinfo=timezone.utc),
        )

    assert len(events) == 2
    assert events[0].commit_hash == "abc123"
    assert events[0].subject == "feat: add feature"
    assert events[0].changed_files == ["archon/agent.py", "archon/tools.py"]
    assert events[1].commit_hash == "def456"
    assert events[0].repo_path == tmp_path


def test_collect_git_activity_subprocess_error():
    from archon.activity import collect_git_activity

    with patch(
        "archon.activity.subprocess.run",
        side_effect=subprocess.CalledProcessError(128, "git"),
    ):
        events = collect_git_activity(
            repo_paths=[Path("/tmp/repo")],
            since=datetime(2026, 3, 22, tzinfo=timezone.utc),
        )
    assert events == []


def test_collect_pacman_activity_parses_log(tmp_path):
    from archon.activity import collect_pacman_activity

    log_file = tmp_path / "pacman.log"
    log_file.write_text(
        "[2026-03-23T10:00:00+0000] [ALPM] installed python-httpx (0.27.0-1)\n"
        "[2026-03-23T10:01:00+0000] [ALPM] upgraded linux (6.18.12.arch1-1 -> 6.18.13.arch1-1)\n"
        "[2026-03-23T10:02:00+0000] [ALPM] removed python-flask (3.0.0-1)\n"
        "[2026-03-22T08:00:00+0000] [ALPM] installed old-package (1.0-1)\n"
    )
    events = collect_pacman_activity(
        since=datetime(2026, 3, 23, tzinfo=timezone.utc),
        log_path=log_file,
    )
    assert len(events) == 3
    assert events[0].action == "installed"
    assert events[0].package == "python-httpx"
    assert events[0].version == "0.27.0-1"
    assert events[1].action == "upgraded"
    assert events[1].package == "linux"
    assert events[1].version == "6.18.13.arch1-1"
    assert events[2].action == "removed"
    assert events[2].package == "python-flask"


def test_collect_pacman_activity_missing_log():
    from archon.activity import collect_pacman_activity

    events = collect_pacman_activity(
        since=datetime(2026, 3, 22, tzinfo=timezone.utc),
        log_path=Path("/nonexistent/pacman.log"),
    )
    assert events == []


def test_collect_working_tree_summary(tmp_path):
    from archon.activity import collect_working_tree_summary

    status_output = (
        " M archon/agent.py\n"
        " M archon/tools.py\n"
        "?? new_file.py\n"
        "A  staged_file.py\n"
        "MM both.py\n"
    )
    branch_output = "master\n"
    stash_output = "stash@{0}: WIP on master\nstash@{1}: WIP on feature\n"

    def mock_run(cmd, **kwargs):
        result = MagicMock()
        result.returncode = 0
        if "status" in cmd:
            result.stdout = status_output
        elif "rev-parse" in cmd:
            result.stdout = branch_output
        elif "stash" in cmd:
            result.stdout = stash_output
        else:
            result.stdout = ""
        return result

    with patch("archon.activity.subprocess.run", side_effect=mock_run):
        trees = collect_working_tree_summary(repo_paths=[tmp_path])

    assert len(trees) == 1
    wt = trees[0]
    assert wt.repo_path == tmp_path
    assert wt.branch == "master"
    assert wt.dirty is True
    assert wt.untracked == 1
    assert wt.modified >= 2
    assert wt.staged >= 1
    assert wt.stash_count == 2


def test_collect_system_stats(tmp_path):
    from archon.activity import collect_system_stats

    uptime_file = tmp_path / "uptime"
    uptime_file.write_text("259200.00 518400.00\n")

    loadavg_file = tmp_path / "loadavg"
    loadavg_file.write_text("0.40 0.30 0.20 1/200 12345\n")

    meminfo_file = tmp_path / "meminfo"
    meminfo_file.write_text(
        "MemTotal:       33554432 kB\n"
        "MemFree:         8388608 kB\n"
        "MemAvailable:   16777216 kB\n"
        "Buffers:         1048576 kB\n"
        "Cached:          8388608 kB\n"
    )

    mock_disk = MagicMock()
    mock_disk.total = 500 * 1024**3
    mock_disk.used = 120 * 1024**3

    with patch("archon.activity.shutil.disk_usage", return_value=mock_disk):
        snap = collect_system_stats(proc_dir=tmp_path)

    assert snap is not None
    assert abs(snap.uptime_seconds - 259200.0) < 1
    assert abs(snap.load_1 - 0.4) < 0.01
    assert abs(snap.mem_total_gb - 32.0) < 0.1
    assert abs(snap.disk_used_gb - 120.0) < 0.1
    assert abs(snap.disk_total_gb - 500.0) < 0.1


def _make_full_summary():
    from archon.activity import (
        ActivitySummary,
        PackageEvent,
        PackageSummary,
        RepoSummary,
        SystemSnapshot,
        WorkingTreeSummary,
    )

    return ActivitySummary(
        scanned_at=datetime(2026, 3, 23, 14, 30, tzinfo=timezone.utc),
        since=datetime(2026, 3, 22, 9, 15, tzinfo=timezone.utc),
        git=[
            RepoSummary(
                repo_path=Path("/home/user/archon"),
                commit_count=8,
                top_changed_dirs=[("archon", 5), ("tests", 3)],
                branches=["master"],
                last_commit_subject="feat: add feature",
            ),
        ],
        packages=PackageSummary(
            installed=[
                PackageEvent(
                    "installed",
                    "python-httpx",
                    "0.27.0",
                    datetime(2026, 3, 23, tzinfo=timezone.utc),
                ),
                PackageEvent(
                    "installed",
                    "python-pydantic",
                    "2.6.0",
                    datetime(2026, 3, 23, tzinfo=timezone.utc),
                ),
            ],
            removed=[],
            upgraded=[
                PackageEvent(
                    "upgraded",
                    "linux",
                    "6.18.13.arch1-1",
                    datetime(2026, 3, 23, tzinfo=timezone.utc),
                ),
            ],
        ),
        working_trees=[
            WorkingTreeSummary(Path("/home/user/archon"), "master", True, 1, 3, 0, 0),
        ],
        system=SystemSnapshot(259200, 0.4, 0.3, 0.2, 8.2, 32.0, 120.0, 500.0),
    )


def test_aggregate_snapshot_basic():
    from archon.activity import (
        GitEvent,
        PackageEvent,
        WorkingTreeSummary,
        aggregate_snapshot,
    )

    repo = Path("/repo")
    git_events = [
        GitEvent(
            repo_path=repo,
            commit_hash="abc",
            timestamp=datetime(2026, 3, 23, tzinfo=timezone.utc),
            subject="feat: X",
            changed_files=["archon/agent.py", "archon/tools.py", "tests/test.py"],
        ),
        GitEvent(
            repo_path=repo,
            commit_hash="def",
            timestamp=datetime(2026, 3, 23, tzinfo=timezone.utc),
            subject="fix: Y",
            changed_files=["archon/agent.py"],
        ),
    ]
    package_events = [
        PackageEvent(
            action="installed",
            package="python-httpx",
            version="0.27.0",
            timestamp=datetime(2026, 3, 23, tzinfo=timezone.utc),
        )
    ]
    working_trees = [WorkingTreeSummary(repo, "master", True, 1, 2, 0, 0)]

    summary = aggregate_snapshot(
        git_events=git_events,
        package_events=package_events,
        working_trees=working_trees,
        system=None,
    )

    assert len(summary.git) == 1
    assert summary.git[0].commit_count == 2
    assert ("archon", 3) in summary.git[0].top_changed_dirs
    assert summary.packages.installed[0].package == "python-httpx"
    assert summary.working_trees[0].branch == "master"


def test_store_and_load_last_session(tmp_path):
    from archon.activity import load_last_session, save_last_session, store_snapshot

    activity_dir = tmp_path / "activity"
    summary = _make_full_summary()
    store_snapshot(activity_dir, summary)
    assert list((activity_dir / "snapshots").glob("*.jsonl"))

    ts = datetime(2026, 3, 23, 14, 30, tzinfo=timezone.utc)
    save_last_session(activity_dir, ts)
    loaded = load_last_session(activity_dir)
    assert loaded == ts


def test_cleanup_old_snapshots(tmp_path):
    from archon.activity import cleanup_old_snapshots
    import os
    import time

    activity_dir = tmp_path / "activity"
    snapshots_dir = activity_dir / "snapshots"
    snapshots_dir.mkdir(parents=True)
    old_file = snapshots_dir / "old.jsonl"
    new_file = snapshots_dir / "new.jsonl"
    old_file.write_text("{}\n")
    new_file.write_text("{}\n")

    now = time.time()
    old_age = now - (40 * 24 * 60 * 60)
    os.utime(old_file, (old_age, old_age))

    cleanup_old_snapshots(activity_dir, retention_days=30)

    assert not old_file.exists()
    assert new_file.exists()


def test_code_only_summarizer_basic():
    from archon.activity import CodeOnlySummarizer

    summary = _make_full_summary()
    summarizer = CodeOnlySummarizer()
    text = summarizer.summarize(summary, token_budget=200)
    assert "[Recent Activity" in text
    assert "archon" in text.lower()
    assert "httpx" in text.lower() or "python-httpx" in text.lower()
    assert len(text) < 1000


def test_code_only_summarizer_empty_summary():
    from archon.activity import ActivitySummary, CodeOnlySummarizer, PackageSummary

    summary = ActivitySummary(
        scanned_at=datetime(2026, 3, 23, tzinfo=timezone.utc),
        since=datetime(2026, 3, 22, tzinfo=timezone.utc),
        git=[],
        packages=PackageSummary([], [], []),
        working_trees=[],
        system=None,
    )
    summarizer = CodeOnlySummarizer()
    assert summarizer.summarize(summary, token_budget=200) == ""


def test_format_activity_report_and_build_injection_text():
    from archon.activity import build_injection_text, format_activity_report

    summary = _make_full_summary()
    report = format_activity_report(summary)
    assert "[Recent Activity" in report
    assert "Working tree" in report
    assert "System:" in report

    injection = build_injection_text(summary=summary, token_budget=200)
    assert "[Recent Activity" in injection
    assert build_injection_text(summary=None, token_budget=200) == ""


def test_scan_and_store_runs_collectors(tmp_path):
    from archon.activity import ActivitySummary, scan_and_store
    from archon.config import ActivityConfig

    config = ActivityConfig(enabled=True, repo_paths=[str(tmp_path)])
    activity_dir = tmp_path / "activity"

    mock_git = []
    mock_pacman = []
    from archon.activity import WorkingTreeSummary, SystemSnapshot
    mock_trees = [WorkingTreeSummary(tmp_path, "main", False, 0, 0, 0, 0)]
    mock_system = SystemSnapshot(1000, 0.1, 0.1, 0.1, 4.0, 16.0, 50.0, 200.0)

    with patch("archon.activity.collect_git_activity", return_value=mock_git), \
         patch("archon.activity.collect_pacman_activity", return_value=mock_pacman), \
         patch("archon.activity.collect_working_tree_summary", return_value=mock_trees), \
         patch("archon.activity.collect_system_stats", return_value=mock_system):
        result = scan_and_store(config, activity_dir=activity_dir)

    assert result is not None
    assert isinstance(result, ActivitySummary)
    from archon.activity import load_last_session
    assert load_last_session(activity_dir) is not None
    assert list((activity_dir / "snapshots").glob("*.jsonl"))


def test_scan_and_store_preview_does_not_update_last_session(tmp_path):
    from archon.activity import ActivitySummary, load_last_session, scan_and_store
    from archon.config import ActivityConfig

    config = ActivityConfig(enabled=True, repo_paths=[])
    activity_dir = tmp_path / "activity"

    with patch("archon.activity.collect_git_activity", return_value=[]), \
         patch("archon.activity.collect_pacman_activity", return_value=[]), \
         patch("archon.activity.collect_working_tree_summary", return_value=[]), \
         patch("archon.activity.collect_system_stats", return_value=None):
        result = scan_and_store(
            config,
            activity_dir=activity_dir,
            persist_last_session=False,
        )

    assert result is None or isinstance(result, ActivitySummary)
    assert load_last_session(activity_dir) is None
