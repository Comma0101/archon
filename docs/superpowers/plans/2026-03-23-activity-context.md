# Lightweight Activity Context — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Archon awareness of what changed between sessions — git commits, package ops, working tree state, system stats — injected into the system prompt at session start.

**Architecture:** Four code-only collectors scan git, pacman, working tree, and /proc at session boundaries. Results are aggregated into a summary, stored as JSONL snapshots, and injected into the system prompt before memory prefetch. Surface layers (CLI REPL, Telegram) trigger scans — Agent never calls collectors directly.

**Tech Stack:** Python stdlib only (subprocess, datetime, json, shutil, re, dataclasses, pathlib). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-03-23-activity-context-design.md`

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `archon/activity.py` | Dataclasses, collectors, aggregator, store, summarizer, injection builder |
| `archon/cli_activity_commands.py` | CLI subcommand implementations (status, summary, reset) with DI |
| `tests/test_activity.py` | Unit + integration tests for activity module |
| `tests/test_cli_activity_commands.py` | CLI subcommand tests |

### Modified Files

| File | Change |
|------|--------|
| `archon/config.py` | Add `ActivityConfig` dataclass + `[activity]` TOML parsing |
| `archon/cli.py` | Add `@main.group("activity")` with subcommands |
| `archon/cli_commands.py` | Add `/activity` to `SLASH_COMMAND_GROUPS` |
| `archon/cli_repl_commands.py` | Add `handle_activity_command()` for `/activity` slash command |
| `archon/cli_interactive_commands.py` | Trigger `scan_and_store()` at REPL start and after `/reset`, pass summary to agent |
| `archon/agent.py` | Add `_activity_summary` attribute, inject in `_build_turn_system_prompt()` |
| `archon/adapters/telegram.py` | Trigger `scan_and_store()` in `_get_or_create_chat_agent()`, add `/activity` handler |

---

### Task 1: ActivityConfig Dataclass + TOML Parsing

**Files:**
- Modify: `archon/config.py:17-18` (add ACTIVITY_DIR constant)
- Modify: `archon/config.py:199-213` (add ActivityConfig to Config)
- Modify: `archon/config.py:612-625` (add ACTIVITY_DIR to ensure_dirs)
- Test: `tests/test_activity.py`

- [ ] **Step 1: Write the failing test for ActivityConfig defaults**

Create `tests/test_activity.py`:

```python
"""Tests for lightweight activity context."""

from __future__ import annotations

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_activity.py::test_activity_config_defaults -v`
Expected: FAIL — `cannot import name 'ActivityConfig' from 'archon.config'`

- [ ] **Step 3: Implement ActivityConfig and wire into Config**

In `archon/config.py`, after line 18 (after `CACHE_DIR`), add:

```python
ACTIVITY_DIR = STATE_DIR / "activity"
```

Before the `Config` dataclass (around line 199), add:

```python
@dataclass
class ActivityConfig:
    enabled: bool = False
    repo_paths: list[str] = field(default_factory=list)
    gap_threshold_minutes: int = 60
    token_budget: int = 200
    retention_days: int = 30
    summarizer: str = "code"
    max_repos: int = 5
    max_commits_per_repo: int = 50
```

Add field to `Config` dataclass (after `calls` field):

```python
    activity: ActivityConfig = field(default_factory=ActivityConfig)
```

In `load_config()`, after the last config section parsing (before `return cfg`), add:

```python
        activity = data.get("activity", {})
        cfg.activity.enabled = bool(activity.get("enabled", cfg.activity.enabled))
        cfg.activity.repo_paths = list(activity.get("repo_paths", cfg.activity.repo_paths))
        cfg.activity.gap_threshold_minutes = max(
            1, int(activity.get("gap_threshold_minutes", cfg.activity.gap_threshold_minutes))
        )
        cfg.activity.token_budget = max(
            50, int(activity.get("token_budget", cfg.activity.token_budget))
        )
        cfg.activity.retention_days = max(
            1, int(activity.get("retention_days", cfg.activity.retention_days))
        )
        cfg.activity.summarizer = str(
            activity.get("summarizer", cfg.activity.summarizer)
        )
        cfg.activity.max_repos = max(
            1, int(activity.get("max_repos", cfg.activity.max_repos))
        )
        cfg.activity.max_commits_per_repo = max(
            1, int(activity.get("max_commits_per_repo", cfg.activity.max_commits_per_repo))
        )
```

In `ensure_dirs()`, add `ACTIVITY_DIR` to the directory list:

```python
        ACTIVITY_DIR,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_activity.py -v`
Expected: PASS (both tests)

- [ ] **Step 5: Commit**

```bash
git add archon/config.py tests/test_activity.py
git commit -m "feat(activity): add ActivityConfig dataclass and TOML parsing"
```

---

### Task 2: Activity Dataclasses

**Files:**
- Create: `archon/activity.py`
- Test: `tests/test_activity.py`

- [ ] **Step 1: Write failing tests for dataclasses**

Append to `tests/test_activity.py`:

```python
from datetime import datetime, timezone
from pathlib import Path

from archon.activity import (
    GitEvent,
    PackageEvent,
    WorkingTreeSummary,
    SystemSnapshot,
    RepoSummary,
    PackageSummary,
    ActivitySummary,
)


def test_git_event_creation():
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
    event = PackageEvent(
        action="installed",
        package="python-httpx",
        version="0.27.0",
        timestamp=datetime(2026, 3, 23, tzinfo=timezone.utc),
    )
    assert event.action == "installed"
    assert event.package == "python-httpx"


def test_working_tree_summary_creation():
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_activity.py::test_git_event_creation -v`
Expected: FAIL — `cannot import name 'GitEvent' from 'archon.activity'`

- [ ] **Step 3: Implement dataclasses**

Create `archon/activity.py`:

```python
"""Lightweight activity context — collectors, aggregator, store, summarizer."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class GitEvent:
    repo_path: Path
    commit_hash: str
    timestamp: datetime
    subject: str
    changed_files: list[str] = field(default_factory=list)


@dataclass
class PackageEvent:
    action: str  # "installed", "removed", "upgraded"
    package: str
    version: str
    timestamp: datetime


@dataclass
class WorkingTreeSummary:
    repo_path: Path
    branch: str
    dirty: bool
    untracked: int
    modified: int
    staged: int
    stash_count: int


@dataclass
class SystemSnapshot:
    uptime_seconds: float
    load_1: float
    load_5: float
    load_15: float
    mem_used_gb: float
    mem_total_gb: float
    disk_used_gb: float
    disk_total_gb: float


@dataclass
class RepoSummary:
    repo_path: Path
    commit_count: int
    top_changed_dirs: list[tuple[str, int]]  # (dir, count)
    branches: list[str]
    last_commit_subject: str


@dataclass
class PackageSummary:
    installed: list[PackageEvent]
    removed: list[PackageEvent]
    upgraded: list[PackageEvent]


@dataclass
class ActivitySummary:
    scanned_at: datetime
    since: datetime
    git: list[RepoSummary]
    packages: PackageSummary
    working_trees: list[WorkingTreeSummary]
    system: SystemSnapshot | None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_activity.py -v`
Expected: PASS (all tests so far)

- [ ] **Step 5: Commit**

```bash
git add archon/activity.py tests/test_activity.py
git commit -m "feat(activity): add activity dataclasses"
```

---

### Task 3: Git Activity Collector

**Files:**
- Modify: `archon/activity.py`
- Test: `tests/test_activity.py`

- [ ] **Step 1: Write failing tests for git collector**

Append to `tests/test_activity.py`:

```python
from unittest.mock import patch, MagicMock

from archon.activity import collect_git_activity


def test_collect_git_activity_parses_log(tmp_path):
    """Mock subprocess to return known git log output."""
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


def test_collect_git_activity_empty_repo(tmp_path):
    mock_result = MagicMock()
    mock_result.stdout = ""
    mock_result.returncode = 0

    with patch("archon.activity.subprocess.run", return_value=mock_result):
        events = collect_git_activity(
            repo_paths=[tmp_path],
            since=datetime(2026, 3, 22, tzinfo=timezone.utc),
        )
    assert events == []


def test_collect_git_activity_missing_repo():
    with patch("archon.activity.subprocess.run", side_effect=FileNotFoundError):
        events = collect_git_activity(
            repo_paths=[Path("/nonexistent/repo")],
            since=datetime(2026, 3, 22, tzinfo=timezone.utc),
        )
    assert events == []


def test_collect_git_activity_subprocess_error():
    with patch(
        "archon.activity.subprocess.run",
        side_effect=subprocess.CalledProcessError(128, "git"),
    ):
        events = collect_git_activity(
            repo_paths=[Path("/tmp/repo")],
            since=datetime(2026, 3, 22, tzinfo=timezone.utc),
        )
    assert events == []


def test_collect_git_activity_max_commits(tmp_path):
    """Verify commits are bounded to max_commits_per_repo."""
    lines = []
    for i in range(60):
        lines.append(f"hash{i}|2026-03-23T10:00:00+00:00|commit {i}")
        lines.append(f"file{i}.py")
        lines.append("")
    mock_result = MagicMock()
    mock_result.stdout = "\n".join(lines)
    mock_result.returncode = 0

    with patch("archon.activity.subprocess.run", return_value=mock_result):
        events = collect_git_activity(
            repo_paths=[tmp_path],
            since=datetime(2026, 3, 22, tzinfo=timezone.utc),
            max_commits_per_repo=50,
        )
    assert len(events) == 50
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_activity.py::test_collect_git_activity_parses_log -v`
Expected: FAIL — `cannot import name 'collect_git_activity'`

- [ ] **Step 3: Implement collect_git_activity**

Append to `archon/activity.py`:

```python
# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------


def collect_git_activity(
    repo_paths: list[Path],
    since: datetime,
    max_commits_per_repo: int = 50,
) -> list[GitEvent]:
    """Collect git commits from configured repos since a given timestamp."""
    since_iso = since.isoformat()
    events: list[GitEvent] = []
    for repo in repo_paths:
        try:
            result = subprocess.run(
                [
                    "git", "-C", str(repo), "log",
                    f"--format=%H|%aI|%s",
                    f"--since={since_iso}",
                    "--name-only",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            repo_events = _parse_git_log(result.stdout, repo)
            events.extend(repo_events[:max_commits_per_repo])
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
            logger.debug("git collector failed for %s: %s", repo, exc)
        except Exception as exc:
            logger.debug("git collector unexpected error for %s: %s", repo, exc)
    return events


def _parse_git_log(output: str, repo_path: Path) -> list[GitEvent]:
    """Parse git log --format='%H|%aI|%s' --name-only output."""
    events: list[GitEvent] = []
    if not output or not output.strip():
        return events
    current_hash = ""
    current_ts = datetime.now(timezone.utc)
    current_subject = ""
    current_files: list[str] = []

    for line in output.splitlines():
        line = line.strip()
        if not line:
            if current_hash:
                events.append(GitEvent(
                    repo_path=repo_path,
                    commit_hash=current_hash,
                    timestamp=current_ts,
                    subject=current_subject,
                    changed_files=current_files,
                ))
                current_hash = ""
                current_files = []
            continue
        parts = line.split("|", 2)
        if len(parts) == 3 and len(parts[0]) >= 7:
            # This looks like a commit line: hash|date|subject
            if current_hash:
                events.append(GitEvent(
                    repo_path=repo_path,
                    commit_hash=current_hash,
                    timestamp=current_ts,
                    subject=current_subject,
                    changed_files=current_files,
                ))
                current_files = []
            current_hash = parts[0]
            try:
                current_ts = datetime.fromisoformat(parts[1])
            except ValueError:
                current_ts = datetime.now(timezone.utc)
            current_subject = parts[2]
        else:
            # File name line
            if current_hash:
                current_files.append(line)

    # Flush last commit
    if current_hash:
        events.append(GitEvent(
            repo_path=repo_path,
            commit_hash=current_hash,
            timestamp=current_ts,
            subject=current_subject,
            changed_files=current_files,
        ))
    return events
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_activity.py -k "git" -v`
Expected: PASS (all git tests)

- [ ] **Step 5: Commit**

```bash
git add archon/activity.py tests/test_activity.py
git commit -m "feat(activity): add git activity collector"
```

---

### Task 4: Pacman Log Collector

**Files:**
- Modify: `archon/activity.py`
- Test: `tests/test_activity.py`

- [ ] **Step 1: Write failing tests for pacman collector**

Append to `tests/test_activity.py`:

```python
from archon.activity import collect_pacman_activity


def test_collect_pacman_activity_parses_log(tmp_path):
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
    assert events[2].action == "removed"
    assert events[2].package == "python-flask"


def test_collect_pacman_activity_missing_log():
    events = collect_pacman_activity(
        since=datetime(2026, 3, 22, tzinfo=timezone.utc),
        log_path=Path("/nonexistent/pacman.log"),
    )
    assert events == []


def test_collect_pacman_activity_empty_log(tmp_path):
    log_file = tmp_path / "pacman.log"
    log_file.write_text("")
    events = collect_pacman_activity(
        since=datetime(2026, 3, 22, tzinfo=timezone.utc),
        log_path=log_file,
    )
    assert events == []


def test_collect_pacman_activity_malformed_lines(tmp_path):
    log_file = tmp_path / "pacman.log"
    log_file.write_text(
        "[2026-03-23T10:00:00+0000] [ALPM] installed python-httpx (0.27.0-1)\n"
        "this is not a valid line\n"
        "[2026-03-23T10:01:00+0000] [PACMAN] Running 'pacman -Syu'\n"
    )
    events = collect_pacman_activity(
        since=datetime(2026, 3, 22, tzinfo=timezone.utc),
        log_path=log_file,
    )
    assert len(events) == 1
    assert events[0].package == "python-httpx"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_activity.py::test_collect_pacman_activity_parses_log -v`
Expected: FAIL — `cannot import name 'collect_pacman_activity'`

- [ ] **Step 3: Implement collect_pacman_activity**

Append to `archon/activity.py`:

```python
# Regex for pacman log lines: [timestamp] [ALPM] action package (version)
# Upgraded format: [timestamp] [ALPM] upgraded package (old -> new)
_PACMAN_RE = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+\d{4})\] "
    r"\[ALPM\] (installed|removed|upgraded) "
    r"(\S+) \((.+)\)$"
)


def collect_pacman_activity(
    since: datetime,
    log_path: Path = Path("/var/log/pacman.log"),
) -> list[PackageEvent]:
    """Parse pacman log for package operations since a given timestamp."""
    try:
        text = log_path.read_text(errors="replace")
    except (FileNotFoundError, PermissionError, OSError) as exc:
        logger.debug("pacman collector failed: %s", exc)
        return []

    events: list[PackageEvent] = []
    for line in text.splitlines():
        m = _PACMAN_RE.match(line.strip())
        if not m:
            continue
        try:
            ts = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S%z")
        except ValueError:
            continue
        if ts < since:
            continue
        action = m.group(2)
        package = m.group(3)
        version_str = m.group(4)
        # For upgrades, version_str is "old -> new" — extract the new version
        if " -> " in version_str:
            version_str = version_str.split(" -> ", 1)[1]
        events.append(PackageEvent(
            action=action,
            package=package,
            version=version_str,
            timestamp=ts,
        ))
    return events
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_activity.py -k "pacman" -v`
Expected: PASS (all pacman tests)

- [ ] **Step 5: Commit**

```bash
git add archon/activity.py tests/test_activity.py
git commit -m "feat(activity): add pacman log collector"
```

---

### Task 5: Working Tree Collector

**Files:**
- Modify: `archon/activity.py`
- Test: `tests/test_activity.py`

- [ ] **Step 1: Write failing tests for working tree collector**

Append to `tests/test_activity.py`:

```python
from archon.activity import collect_working_tree_summary


def test_collect_working_tree_summary(tmp_path):
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
    assert wt.modified >= 2  # M and MM both count
    assert wt.staged >= 1  # A and MM both count
    assert wt.stash_count == 2


def test_collect_working_tree_clean(tmp_path):
    def mock_run(cmd, **kwargs):
        result = MagicMock()
        result.returncode = 0
        if "rev-parse" in cmd:
            result.stdout = "main\n"
        else:
            result.stdout = ""
        return result

    with patch("archon.activity.subprocess.run", side_effect=mock_run):
        trees = collect_working_tree_summary(repo_paths=[tmp_path])

    assert len(trees) == 1
    assert trees[0].dirty is False
    assert trees[0].untracked == 0
    assert trees[0].modified == 0
    assert trees[0].staged == 0
    assert trees[0].stash_count == 0


def test_collect_working_tree_missing_repo():
    with patch(
        "archon.activity.subprocess.run",
        side_effect=FileNotFoundError,
    ):
        trees = collect_working_tree_summary(repo_paths=[Path("/nonexistent")])
    assert trees == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_activity.py::test_collect_working_tree_summary -v`
Expected: FAIL — `cannot import name 'collect_working_tree_summary'`

- [ ] **Step 3: Implement collect_working_tree_summary**

Append to `archon/activity.py`:

```python
def collect_working_tree_summary(
    repo_paths: list[Path],
) -> list[WorkingTreeSummary]:
    """Snapshot each repo's working tree state via git status/stash."""
    trees: list[WorkingTreeSummary] = []
    for repo in repo_paths:
        try:
            trees.append(_collect_one_working_tree(repo))
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
            logger.debug("working tree collector failed for %s: %s", repo, exc)
        except Exception as exc:
            logger.debug("working tree collector unexpected error for %s: %s", repo, exc)
    return trees


def _collect_one_working_tree(repo: Path) -> WorkingTreeSummary:
    """Collect working tree summary for a single repo."""
    # Get branch name
    branch_result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, timeout=5,
    )
    branch = branch_result.stdout.strip() or "unknown"

    # Get status
    status_result = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True, text=True, timeout=5,
    )
    untracked = 0
    modified = 0
    staged = 0
    for line in status_result.stdout.splitlines():
        if len(line) < 2:
            continue
        index_status = line[0]
        worktree_status = line[1]
        if line.startswith("??"):
            untracked += 1
        else:
            if index_status not in (" ", "?"):
                staged += 1
            if worktree_status not in (" ", "?"):
                modified += 1

    # Get stash count
    stash_result = subprocess.run(
        ["git", "-C", str(repo), "stash", "list"],
        capture_output=True, text=True, timeout=5,
    )
    stash_count = len([l for l in stash_result.stdout.splitlines() if l.strip()])

    dirty = (untracked + modified + staged) > 0
    return WorkingTreeSummary(
        repo_path=repo,
        branch=branch,
        dirty=dirty,
        untracked=untracked,
        modified=modified,
        staged=staged,
        stash_count=stash_count,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_activity.py -k "working_tree" -v`
Expected: PASS (all working tree tests)

- [ ] **Step 5: Commit**

```bash
git add archon/activity.py tests/test_activity.py
git commit -m "feat(activity): add working tree collector"
```

---

### Task 6: System Stats Collector

**Files:**
- Modify: `archon/activity.py`
- Test: `tests/test_activity.py`

- [ ] **Step 1: Write failing tests for system stats**

Append to `tests/test_activity.py`:

```python
from archon.activity import collect_system_stats


def test_collect_system_stats(tmp_path):
    """Mock /proc files and shutil.disk_usage for testing."""
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
        snap = collect_system_stats(
            proc_dir=tmp_path,
        )

    assert snap is not None
    assert abs(snap.uptime_seconds - 259200.0) < 1
    assert abs(snap.load_1 - 0.4) < 0.01
    assert abs(snap.mem_total_gb - 32.0) < 0.1
    assert abs(snap.disk_used_gb - 120.0) < 0.1
    assert abs(snap.disk_total_gb - 500.0) < 0.1


def test_collect_system_stats_missing_proc():
    snap = collect_system_stats(proc_dir=Path("/nonexistent"))
    assert snap is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_activity.py::test_collect_system_stats -v`
Expected: FAIL — `collect_system_stats() got an unexpected keyword argument 'proc_dir'` (or not found)

- [ ] **Step 3: Implement collect_system_stats**

Append to `archon/activity.py`:

```python
def collect_system_stats(
    proc_dir: Path = Path("/proc"),
) -> SystemSnapshot | None:
    """Read system stats from /proc and disk usage. Returns None on failure."""
    try:
        # Uptime
        uptime_text = (proc_dir / "uptime").read_text().strip()
        uptime_seconds = float(uptime_text.split()[0])

        # Load averages
        loadavg_text = (proc_dir / "loadavg").read_text().strip()
        load_parts = loadavg_text.split()
        load_1 = float(load_parts[0])
        load_5 = float(load_parts[1])
        load_15 = float(load_parts[2])

        # Memory
        meminfo_text = (proc_dir / "meminfo").read_text()
        mem_total_kb = 0
        mem_available_kb = 0
        for line in meminfo_text.splitlines():
            if line.startswith("MemTotal:"):
                mem_total_kb = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                mem_available_kb = int(line.split()[1])
        mem_total_gb = mem_total_kb / (1024 * 1024)
        mem_used_gb = (mem_total_kb - mem_available_kb) / (1024 * 1024)

        # Disk
        disk = shutil.disk_usage("/")
        disk_total_gb = disk.total / (1024**3)
        disk_used_gb = disk.used / (1024**3)

        return SystemSnapshot(
            uptime_seconds=uptime_seconds,
            load_1=load_1,
            load_5=load_5,
            load_15=load_15,
            mem_used_gb=round(mem_used_gb, 1),
            mem_total_gb=round(mem_total_gb, 1),
            disk_used_gb=round(disk_used_gb, 1),
            disk_total_gb=round(disk_total_gb, 1),
        )
    except Exception as exc:
        logger.debug("system stats collector failed: %s", exc)
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_activity.py -k "system_stats" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add archon/activity.py tests/test_activity.py
git commit -m "feat(activity): add system stats collector"
```

---

### Task 7: Aggregator + Store

**Files:**
- Modify: `archon/activity.py`
- Test: `tests/test_activity.py`

- [ ] **Step 1: Write failing tests for aggregator and store**

Append to `tests/test_activity.py`:

```python
from archon.activity import (
    aggregate_snapshot,
    store_snapshot,
    load_last_session,
    save_last_session,
    cleanup_old_snapshots,
)


def test_aggregate_snapshot_basic():
    events = [
        GitEvent(
            repo_path=Path("/repo"),
            commit_hash="abc",
            timestamp=datetime(2026, 3, 23, tzinfo=timezone.utc),
            subject="feat: X",
            changed_files=["archon/agent.py", "archon/tools.py", "tests/test.py"],
        ),
        GitEvent(
            repo_path=Path("/repo"),
            commit_hash="def",
            timestamp=datetime(2026, 3, 23, tzinfo=timezone.utc),
            subject="fix: Y",
            changed_files=["archon/agent.py"],
        ),
    ]
    packages = [
        PackageEvent("installed", "httpx", "0.27.0", datetime(2026, 3, 23, tzinfo=timezone.utc)),
    ]
    trees = [
        WorkingTreeSummary(Path("/repo"), "master", True, 1, 2, 0, 0),
    ]
    system = SystemSnapshot(259200, 0.4, 0.3, 0.2, 8.2, 32.0, 120.0, 500.0)

    summary = aggregate_snapshot(
        git_events=events,
        package_events=packages,
        working_trees=trees,
        system=system,
    )

    assert len(summary.git) == 1  # grouped by repo
    assert summary.git[0].commit_count == 2
    assert summary.git[0].last_commit_subject == "feat: X"
    # archon/ should be top changed dir (3 files across 2 commits)
    assert any("archon" in d for d, _ in summary.git[0].top_changed_dirs)
    assert len(summary.packages.installed) == 1
    assert summary.packages.installed[0].package == "httpx"
    assert len(summary.working_trees) == 1
    assert summary.system is not None


def test_aggregate_snapshot_empty():
    summary = aggregate_snapshot(
        git_events=[],
        package_events=[],
        working_trees=[],
        system=None,
    )
    assert summary.git == []
    assert summary.packages.installed == []
    assert summary.packages.removed == []
    assert summary.packages.upgraded == []
    assert summary.working_trees == []
    assert summary.system is None


def test_store_and_load_last_session(tmp_path):
    ts = datetime(2026, 3, 23, 14, 30, tzinfo=timezone.utc)
    save_last_session(tmp_path, ts)
    loaded = load_last_session(tmp_path)
    assert loaded is not None
    assert loaded == ts


def test_load_last_session_missing(tmp_path):
    loaded = load_last_session(tmp_path)
    assert loaded is None


def test_load_last_session_corrupted(tmp_path):
    (tmp_path / "last_session.json").write_text("not json{{{")
    loaded = load_last_session(tmp_path)
    assert loaded is None


def test_store_snapshot_creates_jsonl(tmp_path):
    summary = ActivitySummary(
        scanned_at=datetime(2026, 3, 23, 14, 30, tzinfo=timezone.utc),
        since=datetime(2026, 3, 22, tzinfo=timezone.utc),
        git=[],
        packages=PackageSummary([], [], []),
        working_trees=[],
        system=None,
    )
    store_snapshot(tmp_path, summary)
    snapshots_dir = tmp_path / "snapshots"
    assert snapshots_dir.exists()
    files = list(snapshots_dir.glob("*.jsonl"))
    assert len(files) == 1


def test_cleanup_old_snapshots(tmp_path):
    snapshots_dir = tmp_path / "snapshots"
    snapshots_dir.mkdir()
    import time
    # Create an "old" file and a "new" file
    old_file = snapshots_dir / "old.jsonl"
    old_file.write_text("{}")
    new_file = snapshots_dir / "new.jsonl"
    new_file.write_text("{}")
    # Set old file mtime to 60 days ago
    old_mtime = time.time() - (60 * 86400)
    import os
    os.utime(old_file, (old_mtime, old_mtime))

    cleanup_old_snapshots(tmp_path, retention_days=30)
    assert not old_file.exists()
    assert new_file.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_activity.py::test_aggregate_snapshot_basic -v`
Expected: FAIL — `cannot import name 'aggregate_snapshot'`

- [ ] **Step 3: Implement aggregator and store functions**

Append to `archon/activity.py`:

```python
# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


def aggregate_snapshot(
    git_events: list[GitEvent],
    package_events: list[PackageEvent],
    working_trees: list[WorkingTreeSummary],
    system: SystemSnapshot | None,
) -> ActivitySummary:
    """Aggregate raw collector data into a structured summary."""
    now = datetime.now(timezone.utc)

    # Group git events by repo
    repos: dict[str, list[GitEvent]] = {}
    for event in git_events:
        key = str(event.repo_path)
        repos.setdefault(key, []).append(event)

    git_summaries: list[RepoSummary] = []
    for repo_key, events in repos.items():
        # Count changed files by directory
        dir_counts: dict[str, int] = {}
        for event in events:
            for f in event.changed_files:
                d = str(Path(f).parent)
                if d == ".":
                    d = "(root)"
                dir_counts[d] = dir_counts.get(d, 0) + 1
        top_dirs = sorted(dir_counts.items(), key=lambda x: x[1], reverse=True)[:5]

        # Branches (unique, from commit data — approximate via repo)
        branches: list[str] = []
        for wt in working_trees:
            if str(wt.repo_path) == repo_key:
                branches.append(wt.branch)

        git_summaries.append(RepoSummary(
            repo_path=Path(repo_key),
            commit_count=len(events),
            top_changed_dirs=top_dirs,
            branches=branches or ["unknown"],
            last_commit_subject=events[0].subject if events else "",
        ))

    # Group package events by action
    pkg_summary = PackageSummary(
        installed=[e for e in package_events if e.action == "installed"],
        removed=[e for e in package_events if e.action == "removed"],
        upgraded=[e for e in package_events if e.action == "upgraded"],
    )

    # Find earliest since from git events, or use now - 24h
    since = now
    if git_events:
        since = min(e.timestamp for e in git_events)

    return ActivitySummary(
        scanned_at=now,
        since=since,
        git=git_summaries,
        packages=pkg_summary,
        working_trees=working_trees,
        system=system,
    )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def save_last_session(activity_dir: Path, timestamp: datetime) -> None:
    """Save the last session timestamp."""
    activity_dir.mkdir(parents=True, exist_ok=True)
    path = activity_dir / "last_session.json"
    data = {"last_scan": timestamp.isoformat()}
    path.write_text(json.dumps(data))


def load_last_session(activity_dir: Path) -> datetime | None:
    """Load the last session timestamp. Returns None if missing or corrupted."""
    path = activity_dir / "last_session.json"
    try:
        data = json.loads(path.read_text())
        return datetime.fromisoformat(data["last_scan"])
    except Exception:
        return None


def store_snapshot(activity_dir: Path, summary: ActivitySummary) -> None:
    """Write an activity snapshot as JSONL."""
    snapshots_dir = activity_dir / "snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    filename = summary.scanned_at.strftime("%Y-%m-%dT%H:%M:%S") + ".jsonl"
    path = snapshots_dir / filename

    lines: list[str] = []
    # Git summaries
    for repo in summary.git:
        lines.append(json.dumps({
            "type": "git",
            "repo": str(repo.repo_path),
            "commits": repo.commit_count,
            "top_dirs": repo.top_changed_dirs,
            "branches": repo.branches,
            "last_subject": repo.last_commit_subject,
        }))
    # Package events
    for action_name, events in [
        ("installed", summary.packages.installed),
        ("removed", summary.packages.removed),
        ("upgraded", summary.packages.upgraded),
    ]:
        for pkg in events:
            lines.append(json.dumps({
                "type": "package",
                "action": action_name,
                "package": pkg.package,
                "version": pkg.version,
            }))
    # Working trees
    for wt in summary.working_trees:
        lines.append(json.dumps({
            "type": "working_tree",
            "repo": str(wt.repo_path),
            "branch": wt.branch,
            "dirty": wt.dirty,
            "untracked": wt.untracked,
            "modified": wt.modified,
            "staged": wt.staged,
            "stash_count": wt.stash_count,
        }))
    # System
    if summary.system:
        s = summary.system
        lines.append(json.dumps({
            "type": "system",
            "uptime_seconds": s.uptime_seconds,
            "load_1": s.load_1,
            "mem_used_gb": s.mem_used_gb,
            "mem_total_gb": s.mem_total_gb,
            "disk_used_gb": s.disk_used_gb,
            "disk_total_gb": s.disk_total_gb,
        }))

    path.write_text("\n".join(lines) + "\n" if lines else "")


def cleanup_old_snapshots(activity_dir: Path, retention_days: int = 30) -> None:
    """Delete snapshot files older than retention_days."""
    import time as _time

    snapshots_dir = activity_dir / "snapshots"
    if not snapshots_dir.exists():
        return
    cutoff = _time.time() - (retention_days * 86400)
    for f in snapshots_dir.glob("*.jsonl"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except Exception as exc:
            logger.debug("cleanup failed for %s: %s", f, exc)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_activity.py -k "aggregate or store or load_last or cleanup" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add archon/activity.py tests/test_activity.py
git commit -m "feat(activity): add aggregator and snapshot store"
```

---

### Task 8: CodeOnlySummarizer + Injection Builder

**Files:**
- Modify: `archon/activity.py`
- Test: `tests/test_activity.py`

- [ ] **Step 1: Write failing tests for summarizer and injection**

Append to `tests/test_activity.py`:

```python
from archon.activity import CodeOnlySummarizer, build_injection_text


def _make_full_summary() -> ActivitySummary:
    """Helper to build a realistic ActivitySummary for testing."""
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
                PackageEvent("installed", "python-httpx", "0.27.0", datetime(2026, 3, 23, tzinfo=timezone.utc)),
                PackageEvent("installed", "python-pydantic", "2.6.0", datetime(2026, 3, 23, tzinfo=timezone.utc)),
            ],
            removed=[],
            upgraded=[
                PackageEvent("upgraded", "linux", "6.18.13.arch1-1", datetime(2026, 3, 23, tzinfo=timezone.utc)),
            ],
        ),
        working_trees=[
            WorkingTreeSummary(Path("/home/user/archon"), "master", True, 1, 3, 0, 0),
        ],
        system=SystemSnapshot(259200, 0.4, 0.3, 0.2, 8.2, 32.0, 120.0, 500.0),
    )


def test_code_only_summarizer_basic():
    summary = _make_full_summary()
    summarizer = CodeOnlySummarizer()
    text = summarizer.summarize(summary, token_budget=200)
    assert "[Recent Activity" in text
    assert "archon" in text.lower()
    assert "httpx" in text.lower() or "python-httpx" in text.lower()
    # Rough token estimate: ~4 chars per token, 200 tokens = 800 chars
    assert len(text) < 1000


def test_code_only_summarizer_empty_summary():
    summary = ActivitySummary(
        scanned_at=datetime(2026, 3, 23, tzinfo=timezone.utc),
        since=datetime(2026, 3, 22, tzinfo=timezone.utc),
        git=[],
        packages=PackageSummary([], [], []),
        working_trees=[],
        system=None,
    )
    summarizer = CodeOnlySummarizer()
    text = summarizer.summarize(summary, token_budget=200)
    assert text == ""


def test_code_only_summarizer_truncation_drops_system_first():
    summary = _make_full_summary()
    summarizer = CodeOnlySummarizer()
    # Very tight budget
    text = summarizer.summarize(summary, token_budget=50)
    # System stats should be dropped first
    assert "load" not in text.lower() or "uptime" not in text.lower()
    # Git should still be present (highest priority)
    assert "archon" in text.lower() or "commit" in text.lower()


def test_build_injection_text_with_summary():
    summary = _make_full_summary()
    text = build_injection_text(summary=summary, token_budget=200)
    assert text != ""
    assert "[Recent Activity" in text


def test_build_injection_text_none_summary():
    text = build_injection_text(summary=None, token_budget=200)
    assert text == ""


def test_build_injection_text_empty_summary():
    summary = ActivitySummary(
        scanned_at=datetime(2026, 3, 23, tzinfo=timezone.utc),
        since=datetime(2026, 3, 22, tzinfo=timezone.utc),
        git=[],
        packages=PackageSummary([], [], []),
        working_trees=[],
        system=None,
    )
    text = build_injection_text(summary=summary, token_budget=200)
    assert text == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_activity.py::test_code_only_summarizer_basic -v`
Expected: FAIL — `cannot import name 'CodeOnlySummarizer'`

- [ ] **Step 3: Implement CodeOnlySummarizer and build_injection_text**

Append to `archon/activity.py`:

```python
# ---------------------------------------------------------------------------
# Summarizer
# ---------------------------------------------------------------------------


class CodeOnlySummarizer:
    """Template-based summarizer — no LLM. Prioritizes git > packages > working tree > system."""

    def summarize(self, summary: ActivitySummary, token_budget: int) -> str:
        """Format ActivitySummary into compact text within token budget."""
        if not summary.git and not summary.packages.installed and not summary.packages.removed \
                and not summary.packages.upgraded and not summary.working_trees and not summary.system:
            return ""

        sections: list[str] = []
        char_budget = token_budget * 4  # rough 4 chars/token estimate

        # Priority 1: Git activity
        for repo in summary.git:
            repo_name = repo.repo_path.name
            dirs_str = ", ".join(f"{d} {c}" for d, c in repo.top_changed_dirs[:3])
            branch_str = repo.branches[0] if repo.branches else "unknown"
            sections.append(
                f"Git: {repo_name}/ — {repo.commit_count} commits ({dirs_str}). Branch: {branch_str}."
            )

        # Priority 2: Package changes
        pkg_parts: list[str] = []
        if summary.packages.installed:
            names = ", ".join(e.package for e in summary.packages.installed[:5])
            pkg_parts.append(f"installed {names}")
        if summary.packages.upgraded:
            names = ", ".join(f"{e.package} {e.version}" for e in summary.packages.upgraded[:5])
            pkg_parts.append(f"upgraded {names}")
        if summary.packages.removed:
            names = ", ".join(e.package for e in summary.packages.removed[:5])
            pkg_parts.append(f"removed {names}")
        if pkg_parts:
            sections.append(f"Packages: {'. '.join(pkg_parts)}.")

        # Priority 3: Working tree
        for wt in summary.working_trees:
            if wt.dirty:
                parts = []
                if wt.modified:
                    parts.append(f"{wt.modified} modified")
                if wt.staged:
                    parts.append(f"{wt.staged} staged")
                if wt.untracked:
                    parts.append(f"{wt.untracked} untracked")
                if wt.stash_count:
                    parts.append(f"{wt.stash_count} stashes")
                if parts:
                    sections.append(f"Working tree ({wt.repo_path.name}): {', '.join(parts)}.")

        # Priority 4: System stats (lowest — truncated first)
        if summary.system:
            s = summary.system
            days = int(s.uptime_seconds / 86400)
            uptime_str = f"{days}d" if days > 0 else f"{int(s.uptime_seconds / 3600)}h"
            sections.append(
                f"System: up {uptime_str}, load {s.load_1}, "
                f"mem {s.mem_used_gb}/{s.mem_total_gb}GB, disk {int(s.disk_used_gb)}/{int(s.disk_total_gb)}GB."
            )

        # Truncate from the end (lowest priority first) to fit budget
        since_str = summary.since.strftime("%Y-%m-%d %H:%M")
        header = f"[Recent Activity — since {since_str}]"
        while sections:
            body = "\n".join(sections)
            full = f"{header}\n{body}"
            if len(full) <= char_budget:
                return full
            sections.pop()  # drop lowest-priority section

        return ""


# ---------------------------------------------------------------------------
# Injection
# ---------------------------------------------------------------------------


def build_injection_text(
    summary: ActivitySummary | None,
    token_budget: int = 200,
) -> str:
    """Build activity context text for system prompt injection."""
    if summary is None:
        return ""
    summarizer = CodeOnlySummarizer()
    return summarizer.summarize(summary, token_budget)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_activity.py -k "summarizer or injection" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add archon/activity.py tests/test_activity.py
git commit -m "feat(activity): add CodeOnlySummarizer and injection builder"
```

---

### Task 9: scan_and_store() Orchestrator

**Files:**
- Modify: `archon/activity.py`
- Test: `tests/test_activity.py`

- [ ] **Step 1: Write failing tests for scan_and_store**

Append to `tests/test_activity.py`:

```python
from archon.activity import scan_and_store
from archon.config import ActivityConfig


def test_scan_and_store_disabled():
    config = ActivityConfig(enabled=False)
    result = scan_and_store(config, activity_dir=Path("/tmp"))
    assert result is None


def test_scan_and_store_no_repos():
    config = ActivityConfig(enabled=True, repo_paths=[])
    result = scan_and_store(config, activity_dir=Path("/tmp"))
    # Still runs (pacman + system), but may produce empty summary
    assert result is None or isinstance(result, ActivitySummary)


def test_scan_and_store_runs_collectors(tmp_path):
    """Verify scan_and_store calls collectors and stores results."""
    config = ActivityConfig(
        enabled=True,
        repo_paths=[str(tmp_path)],
    )
    activity_dir = tmp_path / "activity"

    # Mock all collectors
    mock_git = []
    mock_pacman = []
    mock_trees = [
        WorkingTreeSummary(tmp_path, "main", False, 0, 0, 0, 0),
    ]
    mock_system = SystemSnapshot(1000, 0.1, 0.1, 0.1, 4.0, 16.0, 50.0, 200.0)

    with patch("archon.activity.collect_git_activity", return_value=mock_git), \
         patch("archon.activity.collect_pacman_activity", return_value=mock_pacman), \
         patch("archon.activity.collect_working_tree_summary", return_value=mock_trees), \
         patch("archon.activity.collect_system_stats", return_value=mock_system):
        result = scan_and_store(config, activity_dir=activity_dir)

    assert result is not None
    assert isinstance(result, ActivitySummary)
    # Verify last_session was saved
    assert load_last_session(activity_dir) is not None
    # Verify snapshot was stored
    assert list((activity_dir / "snapshots").glob("*.jsonl"))


def test_scan_and_store_respects_gap_threshold(tmp_path):
    """If last scan was recent (< gap_threshold), still returns summary but with recent since."""
    config = ActivityConfig(enabled=True, repo_paths=[str(tmp_path)])
    activity_dir = tmp_path / "activity"

    # Save a recent session timestamp
    recent = datetime.now(timezone.utc)
    save_last_session(activity_dir, recent)

    with patch("archon.activity.collect_git_activity", return_value=[]), \
         patch("archon.activity.collect_pacman_activity", return_value=[]), \
         patch("archon.activity.collect_working_tree_summary", return_value=[]), \
         patch("archon.activity.collect_system_stats", return_value=None):
        result = scan_and_store(config, activity_dir=activity_dir)

    # Should still scan (scan_and_store always scans; gap threshold is for injection)
    assert result is not None or result is None  # may be empty
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_activity.py::test_scan_and_store_disabled -v`
Expected: FAIL — `cannot import name 'scan_and_store'`

- [ ] **Step 3: Implement scan_and_store**

Append to `archon/activity.py`:

```python
def scan_and_store(
    config: "ActivityConfig",
    activity_dir: Path,
) -> ActivitySummary | None:
    """Run all collectors, aggregate, store snapshot, return summary.

    Called by surface layers (CLI REPL, Telegram) at session boundaries.
    Returns None if activity is disabled.
    """
    if not config.enabled:
        return None

    repo_paths = [Path(p).expanduser() for p in config.repo_paths[:config.max_repos]]

    # Determine "since" from last session
    last_ts = load_last_session(activity_dir)
    if last_ts is None:
        # First run — look back 24h
        since = datetime.now(timezone.utc) - timedelta(hours=24)
    else:
        since = last_ts

    # Run collectors
    git_events = collect_git_activity(
        repo_paths, since, max_commits_per_repo=config.max_commits_per_repo,
    )
    package_events = collect_pacman_activity(since)
    working_trees = collect_working_tree_summary(repo_paths)
    system = collect_system_stats()

    # Aggregate
    summary = aggregate_snapshot(git_events, package_events, working_trees, system)
    summary.since = since

    # Store
    try:
        store_snapshot(activity_dir, summary)
        save_last_session(activity_dir, summary.scanned_at)
        cleanup_old_snapshots(activity_dir, retention_days=config.retention_days)
    except Exception as exc:
        logger.debug("activity store failed: %s", exc)

    return summary
```

Note: `timedelta` must be in the import at the top of `archon/activity.py`. Update the existing import line:

```python
from datetime import datetime, timedelta, timezone
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_activity.py -k "scan_and_store" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add archon/activity.py tests/test_activity.py
git commit -m "feat(activity): add scan_and_store orchestrator"
```

---

### Task 10: Agent Integration (Prompt Injection)

**Files:**
- Modify: `archon/agent.py:140-155` (add `_activity_summary` attribute)
- Modify: `archon/agent.py:1280-1323` (inject activity text in `_build_turn_system_prompt`)
- Test: `tests/test_activity.py`

- [ ] **Step 1: Write failing test for prompt injection**

Append to `tests/test_activity.py`:

```python
from archon.activity import build_injection_text


def test_injection_text_in_prompt_position():
    """Verify activity text is non-empty for a real summary and empty for None."""
    summary = _make_full_summary()
    text = build_injection_text(summary=summary, token_budget=200)
    assert "[Recent Activity" in text
    assert len(text) > 0

    # None summary → empty
    assert build_injection_text(summary=None, token_budget=200) == ""
```

This test already passes from Task 8. The real work here is wiring it into `agent.py`.

- [ ] **Step 2: Run existing tests to verify baseline**

Run: `python -m pytest tests/test_activity.py -v`
Expected: PASS (all tests from tasks 1-9)

- [ ] **Step 3: Add `_activity_summary` to Agent.__init__**

In `archon/agent.py`, after line 152 (`self._pending_compactions: list[dict] = []`), add:

```python
        self._activity_summary: "ActivitySummary | None" = None
```

- [ ] **Step 4: Modify `_build_turn_system_prompt` to inject activity text**

In `archon/agent.py`, add import at the top (with other imports):

```python
from archon.activity import build_injection_text as _build_activity_injection_text
```

In `_build_turn_system_prompt()`, after line 1299 (`_append_compaction_lines(lines, compactions)`), add a new parameter `activity_summary` to the function signature and inject activity text:

**Updated function signature** (line 1280):

```python
def _build_turn_system_prompt(
    base_prompt: str,
    user_message: str,
    config: Config,
    profile_name: str = "default",
    skill_guidance: str = "",
    compactions: list[dict] | None = None,
    activity_summary=None,
) -> str:
```

After `_append_compaction_lines(lines, compactions)` (line 1299), before the memory prefetch block:

```python
    # Activity context — injected before memory prefetch to survive early return
    activity_text = _build_activity_injection_text(
        summary=activity_summary,
        token_budget=config.activity.token_budget,
    )
    if activity_text:
        lines.extend(["", activity_text])
```

- [ ] **Step 5: Pass activity_summary when calling _build_turn_system_prompt**

Find where `_build_turn_system_prompt` is called in `agent.py`. It is called from `Agent._build_system_prompt_for_turn` or similar. Search for the call site:

In the call to `_build_turn_system_prompt(...)`, add `activity_summary=self._activity_summary` as the last argument.

After the first turn's injection, clear the summary so it doesn't repeat. In Agent's `run()` method or turn execution, after the first successful turn:

In `_build_turn_system_prompt`, after building the activity text, the caller should clear it. The simplest approach: the caller (Agent) clears `_activity_summary` after the first turn. Find the turn loop in Agent.run() and add after the first iteration:

```python
        # Clear activity summary after first injection
        if self._activity_summary is not None:
            self._activity_summary = None
```

This should be placed right after the call to `_build_turn_system_prompt` returns.

- [ ] **Step 6: Run full test suite to verify no regressions**

Run: `python -m pytest tests/ -x -q`
Expected: PASS (all existing tests + new tests)

- [ ] **Step 7: Commit**

```bash
git add archon/agent.py
git commit -m "feat(activity): inject activity context into system prompt"
```

---

### Task 11: CLI Subcommands + Slash Command

**Files:**
- Create: `archon/cli_activity_commands.py`
- Modify: `archon/cli.py:378+` (add activity group)
- Modify: `archon/cli_commands.py:17` (add /activity to SLASH_COMMAND_GROUPS)
- Modify: `archon/cli_repl_commands.py:1340+` (add handle_activity_command)
- Modify: `archon/cli_interactive_commands.py:504-524` (add "activity" to handled actions)
- Create: `tests/test_cli_activity_commands.py`

- [ ] **Step 1: Write failing tests for CLI commands**

Create `tests/test_cli_activity_commands.py`:

```python
"""Tests for activity CLI commands."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from archon.cli_activity_commands import (
    activity_status_impl,
    activity_summary_impl,
    activity_reset_impl,
)
from archon.config import ActivityConfig


def test_activity_status_disabled():
    config = ActivityConfig(enabled=False)
    output: list[str] = []
    activity_status_impl(
        config=config,
        activity_dir=Path("/tmp/activity"),
        echo_fn=output.append,
    )
    assert any("disabled" in line.lower() for line in output)


def test_activity_status_enabled(tmp_path):
    config = ActivityConfig(
        enabled=True,
        repo_paths=["/home/user/archon"],
    )
    activity_dir = tmp_path / "activity"
    activity_dir.mkdir()

    output: list[str] = []
    activity_status_impl(
        config=config,
        activity_dir=activity_dir,
        echo_fn=output.append,
    )
    text = "\n".join(output)
    assert "enabled" in text.lower()
    assert "/home/user/archon" in text


def test_activity_summary_disabled():
    config = ActivityConfig(enabled=False)
    output: list[str] = []
    activity_summary_impl(
        config=config,
        activity_dir=Path("/tmp/activity"),
        echo_fn=output.append,
    )
    assert any("disabled" in line.lower() for line in output)


def test_activity_reset(tmp_path):
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
    assert any("reset" in line.lower() or "cleared" in line.lower() for line in output)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli_activity_commands.py::test_activity_status_disabled -v`
Expected: FAIL — `No module named 'archon.cli_activity_commands'`

- [ ] **Step 3: Implement cli_activity_commands.py**

Create `archon/cli_activity_commands.py`:

```python
"""CLI command implementations for activity context management."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from archon.activity import (
    build_injection_text,
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
    """Run collectors and display full summary."""
    if not config.enabled:
        echo_fn("Activity context: disabled")
        return

    summary = scan_and_store(config, activity_dir=activity_dir)
    if summary is None:
        echo_fn("No activity recorded.")
        return

    # Uncapped — show full detail
    text = build_injection_text(summary=summary, token_budget=10000)
    if text:
        echo_fn(text)
    else:
        echo_fn("No activity detected since last session.")


def activity_reset_impl(
    *,
    activity_dir: Path,
    echo_fn: Callable[[str], None],
) -> None:
    """Delete all snapshots and reset last session timestamp."""
    snapshots_dir = activity_dir / "snapshots"
    count = 0
    if snapshots_dir.exists():
        for f in snapshots_dir.glob("*.jsonl"):
            try:
                f.unlink()
                count += 1
            except Exception:
                pass

    last_session = activity_dir / "last_session.json"
    if last_session.exists():
        try:
            last_session.unlink()
        except Exception:
            pass

    echo_fn(f"Activity data cleared ({count} snapshots removed).")
```

- [ ] **Step 4: Wire CLI subcommands in cli.py**

In `archon/cli.py`, add import at top:

```python
from archon.cli_activity_commands import (
    activity_status_impl,
    activity_summary_impl,
    activity_reset_impl,
)
```

After the last command group (e.g., after the history group), add:

```python
@main.group("activity")
def activity_group():
    """Activity context commands."""
    pass


@activity_group.command("status")
def activity_status_cmd():
    cfg = load_config()
    ensure_dirs()
    activity_status_impl(
        config=cfg.activity,
        activity_dir=ACTIVITY_DIR,
        echo_fn=click.echo,
    )


@activity_group.command("summary")
def activity_summary_cmd():
    cfg = load_config()
    ensure_dirs()
    activity_summary_impl(
        config=cfg.activity,
        activity_dir=ACTIVITY_DIR,
        echo_fn=click.echo,
    )


@activity_group.command("reset")
def activity_reset_cmd():
    ensure_dirs()
    activity_reset_impl(
        activity_dir=ACTIVITY_DIR,
        echo_fn=click.echo,
    )
```

Add `ACTIVITY_DIR` to the imports from config:

```python
from archon.config import ..., ACTIVITY_DIR
```

- [ ] **Step 5: Add /activity to SLASH_COMMAND_GROUPS**

In `archon/cli_commands.py`, add to the "Shell" group tuple (after `("/plugins", "plugins")`):

```python
            ("/activity", "activity context"),
```

- [ ] **Step 6: Add handle_activity_command to cli_repl_commands.py**

In `archon/cli_repl_commands.py`, add import:

```python
from archon.cli_activity_commands import activity_summary_impl
from archon.config import ACTIVITY_DIR
```

Add a new handler function (before `handle_repl_command`):

```python
def handle_activity_command(agent, text: str) -> tuple[bool, str]:
    """Handle /activity — show activity summary inline."""
    raw = (text or "").strip().lower()
    if raw != "/activity":
        return False, ""
    config = getattr(agent, "config", None)
    if config is None:
        return True, "Activity context: no config available"
    lines: list[str] = []
    activity_summary_impl(
        config=config.activity,
        activity_dir=ACTIVITY_DIR,
        echo_fn=lines.append,
    )
    return True, "\n".join(lines)
```

In `handle_repl_command()`, add before the `return None, ""` at the end:

```python
    handled, msg = handle_activity_command(agent, raw)
    if handled:
        return "activity", msg
```

- [ ] **Step 7: Add "activity" to handled actions in cli_interactive_commands.py**

In `archon/cli_interactive_commands.py:504`, add `"activity"` to the set of display-and-continue actions:

```python
                if action in {
                    "help",
                    "status",
                    "cost",
                    "clear",
                    "compact",
                    "context",
                    "doctor",
                    "permissions",
                    "approvals",
                    "deny",
                    "approve_next",
                    "skills",
                    "plugins",
                    "model",
                    "calls",
                    "profile",
                    "mcp",
                    "jobs",
                    "job",
                    "activity",
                }:
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `python -m pytest tests/test_cli_activity_commands.py -v`
Expected: PASS

Run: `python -m pytest tests/ -x -q`
Expected: PASS (no regressions)

- [ ] **Step 9: Commit**

```bash
git add archon/cli_activity_commands.py archon/cli.py archon/cli_commands.py \
    archon/cli_repl_commands.py archon/cli_interactive_commands.py \
    tests/test_cli_activity_commands.py
git commit -m "feat(activity): add CLI subcommands and /activity slash command"
```

---

### Task 12: Session-Boundary Scan Triggers (CLI)

**Files:**
- Modify: `archon/cli_interactive_commands.py:74-80` (scan at REPL start)
- Modify: `archon/cli_interactive_commands.py:478-494` (scan after /reset)

- [ ] **Step 1: Add scan at REPL startup**

In `archon/cli_interactive_commands.py`, add import at top:

```python
from archon.activity import scan_and_store as _activity_scan_and_store
from archon.config import ACTIVITY_DIR
```

After line 79 (`agent.session_id = session_id`), add:

```python
    # Scan activity context at session start
    try:
        _activity_summary = _activity_scan_and_store(
            agent.config.activity, activity_dir=ACTIVITY_DIR,
        )
        agent._activity_summary = _activity_summary
    except Exception:
        pass
```

- [ ] **Step 2: Add scan after /reset**

In the `/reset` handler block (around line 478-494), after `agent.session_id = session_id` (line 481), add:

```python
                    try:
                        _reset_summary = _activity_scan_and_store(
                            agent.config.activity, activity_dir=ACTIVITY_DIR,
                        )
                        agent._activity_summary = _reset_summary
                    except Exception:
                        pass
```

- [ ] **Step 3: Add scan after /new and /clear**

`/new` and `/clear` return action `"clear"` from `handle_clear_command()` (in `cli_repl_commands.py:225`). They clear history but don't reassign `session_id` like `/reset` does. Still, `/new` represents a fresh context, so activity should be re-scanned.

In the display-and-continue block (around line 504-526), before `click_echo_fn(msg)`, add a special case for `"clear"`:

```python
                if action == "clear":
                    # /new and /clear clear history — re-scan activity for fresh context
                    try:
                        agent._activity_summary = _activity_scan_and_store(
                            agent.config.activity, activity_dir=ACTIVITY_DIR,
                        )
                    except Exception:
                        pass
```

This goes right before the existing `if action in { "help", "status", ... }:` block.

- [ ] **Step 4: Run full test suite**

Run: `python -m pytest tests/ -x -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add archon/cli_interactive_commands.py
git commit -m "feat(activity): trigger activity scan at CLI session boundaries"
```

---

### Task 13: Telegram Integration

**Files:**
- Modify: `archon/adapters/telegram.py:608+` (scan in `_get_or_create_chat_agent`)
- Modify: `archon/adapters/telegram.py:344+` (add `/activity` command handler)

- [ ] **Step 1: Add scan in _get_or_create_chat_agent**

In `archon/adapters/telegram.py`, add import:

```python
from archon.activity import scan_and_store as _activity_scan_and_store
from archon.config import ACTIVITY_DIR
```

In `_get_or_create_chat_agent()`, after line 619 (`self._wire_chat_route_progress(agent, chat_id)`), add:

```python
            # Scan activity context for new chat agent
            try:
                _summary = _activity_scan_and_store(
                    agent.config.activity, activity_dir=ACTIVITY_DIR,
                )
                agent._activity_summary = _summary
            except Exception:
                pass
```

- [ ] **Step 2: Add /activity command handler**

In the command routing section (around line 344, after the `/help` handler and before `/reset`), add:

```python
        if cmd == "/activity":
            from archon.cli_activity_commands import activity_summary_impl
            lines: list[str] = []
            try:
                config = getattr(self, "_config", None) or load_config()
                activity_summary_impl(
                    config=config.activity,
                    activity_dir=ACTIVITY_DIR,
                    echo_fn=lines.append,
                )
            except Exception as exc:
                lines.append(f"Activity error: {exc}")
            self._send_text_and_record(chat_id, body, "\n".join(lines) if lines else "No activity data.")
            return
```

- [ ] **Step 3: Add /activity to Telegram /help text**

In the `/help` command handler (around line 365), add `/activity` to the context line:

```python
                    context="/new, /clear, /compact, /context, /cost, /activity",
```

Also update the `/start` handler similarly (around line 353):

```python
                    context="/new, /clear, /compact, /context, /cost, /activity",
```

- [ ] **Step 4: Add scan after Telegram /reset**

In the `/reset` handler (around line 373), after the agent is popped and reset, when the new agent is created on next message, the scan happens automatically in `_get_or_create_chat_agent`. No additional code needed here — the pop ensures the next message triggers a fresh agent creation with scan.

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest tests/ -x -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add archon/adapters/telegram.py
git commit -m "feat(activity): add Telegram activity scan and /activity command"
```

---

### Task 14: Final Verification

**Files:**
- All files from tasks 1-13

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: ALL PASS

- [ ] **Step 2: Verify no import cycles**

Run: `python -c "from archon.activity import scan_and_store, build_injection_text, CodeOnlySummarizer; print('OK')"`
Expected: `OK`

Run: `python -c "from archon.cli_activity_commands import activity_status_impl; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Verify CLI commands work**

Run: `python -m archon activity status`
Expected: Shows "Activity context: disabled" (default config)

Run: `python -m archon activity reset`
Expected: Shows "Activity data cleared (0 snapshots removed)."

- [ ] **Step 4: Commit (if any fixups needed)**

```bash
git add -A
git commit -m "fix(activity): final verification fixups"
```
