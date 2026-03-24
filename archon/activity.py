"""Lightweight activity context — collectors, storage, summarization, and injection."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass
class GitEvent:
    repo_path: Path
    commit_hash: str
    timestamp: datetime
    subject: str
    changed_files: list[str] = field(default_factory=list)


@dataclass
class PackageEvent:
    action: str
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
    top_changed_dirs: list[tuple[str, int]]
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


_PACMAN_RE = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+\d{4})\] "
    r"\[ALPM\] (installed|removed|upgraded) "
    r"(\S+) \((.+)\)$"
)


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
                    "git",
                    "-C",
                    str(repo),
                    "log",
                    f"--format=%H|%aI|%s",
                    f"--since={since_iso}",
                    "--name-only",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            continue
        repo_events = _parse_git_log(result.stdout, repo)
        events.extend(repo_events[:max(1, int(max_commits_per_repo))])
    return events


def _parse_git_log(output: str, repo_path: Path) -> list[GitEvent]:
    events: list[GitEvent] = []
    current_hash = ""
    current_ts: datetime | None = None
    current_subject = ""
    current_files: list[str] = []

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            if current_hash:
                events.append(
                    GitEvent(
                        repo_path=repo_path,
                        commit_hash=current_hash,
                        timestamp=current_ts or datetime.now().astimezone(),
                        subject=current_subject,
                        changed_files=current_files,
                    )
                )
                current_hash = ""
                current_ts = None
                current_subject = ""
                current_files = []
            continue

        parts = line.split("|", 2)
        if len(parts) == 3 and len(parts[0]) >= 6:
            if current_hash:
                events.append(
                    GitEvent(
                        repo_path=repo_path,
                        commit_hash=current_hash,
                        timestamp=current_ts or datetime.now().astimezone(),
                        subject=current_subject,
                        changed_files=current_files,
                    )
                )
                current_files = []
            current_hash = parts[0]
            try:
                current_ts = datetime.fromisoformat(parts[1])
            except ValueError:
                current_ts = datetime.now().astimezone()
            current_subject = parts[2]
            continue

        if current_hash:
            current_files.append(line)

    if current_hash:
        events.append(
            GitEvent(
                repo_path=repo_path,
                commit_hash=current_hash,
                timestamp=current_ts or datetime.now().astimezone(),
                subject=current_subject,
                changed_files=current_files,
            )
        )
    return events


def collect_pacman_activity(
    since: datetime,
    log_path: Path = Path("/var/log/pacman.log"),
) -> list[PackageEvent]:
    """Parse pacman log for package operations since a given timestamp."""
    try:
        text = log_path.read_text(errors="replace")
    except (FileNotFoundError, PermissionError, OSError):
        return []

    events: list[PackageEvent] = []
    for line in text.splitlines():
        match = _PACMAN_RE.match(line.strip())
        if not match:
            continue
        try:
            ts = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S%z")
        except ValueError:
            continue
        if ts < since:
            continue
        version = match.group(4)
        if " -> " in version:
            version = version.split(" -> ", 1)[1]
        events.append(
            PackageEvent(
                action=match.group(2),
                package=match.group(3),
                version=version,
                timestamp=ts,
            )
        )
    return events


def collect_working_tree_summary(repo_paths: list[Path]) -> list[WorkingTreeSummary]:
    """Snapshot each repo's working tree state via git status/stash."""
    summaries: list[WorkingTreeSummary] = []
    for repo in repo_paths:
        try:
            summaries.append(_collect_one_working_tree(repo))
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return summaries


def _collect_one_working_tree(repo: Path) -> WorkingTreeSummary:
    branch_result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    branch = branch_result.stdout.strip() or "unknown"

    status_result = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=5,
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
            continue
        if index_status not in (" ", "?"):
            staged += 1
        if worktree_status not in (" ", "?"):
            modified += 1

    stash_result = subprocess.run(
        ["git", "-C", str(repo), "stash", "list"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    stash_count = len([line for line in stash_result.stdout.splitlines() if line.strip()])
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


def collect_system_stats(proc_dir: Path = Path("/proc")) -> SystemSnapshot | None:
    """Read system stats from /proc and disk usage."""
    try:
        uptime_text = (proc_dir / "uptime").read_text().strip()
        uptime_seconds = float(uptime_text.split()[0])

        loadavg_text = (proc_dir / "loadavg").read_text().strip()
        load_parts = loadavg_text.split()
        load_1 = float(load_parts[0])
        load_5 = float(load_parts[1])
        load_15 = float(load_parts[2])

        meminfo_text = (proc_dir / "meminfo").read_text()
        mem_total_kb = 0
        mem_available_kb = 0
        for line in meminfo_text.splitlines():
            if line.startswith("MemTotal:"):
                mem_total_kb = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                mem_available_kb = int(line.split()[1])

        disk = shutil.disk_usage("/")
        return SystemSnapshot(
            uptime_seconds=uptime_seconds,
            load_1=load_1,
            load_5=load_5,
            load_15=load_15,
            mem_used_gb=round((mem_total_kb - mem_available_kb) / (1024 * 1024), 1),
            mem_total_gb=round(mem_total_kb / (1024 * 1024), 1),
            disk_used_gb=round(disk.used / (1024**3), 1),
            disk_total_gb=round(disk.total / (1024**3), 1),
        )
    except Exception:
        return None


def aggregate_snapshot(
    git_events: list[GitEvent],
    package_events: list[PackageEvent],
    working_trees: list[WorkingTreeSummary],
    system: SystemSnapshot | None,
) -> ActivitySummary:
    """Aggregate raw collector outputs into a compact summary."""
    repo_map: dict[Path, list[GitEvent]] = {}
    for event in git_events:
        repo_map.setdefault(event.repo_path, []).append(event)

    repo_summaries: list[RepoSummary] = []
    working_tree_by_repo = {tree.repo_path: tree for tree in working_trees}
    for repo_path, events in repo_map.items():
        dir_counts: dict[str, int] = {}
        for event in events:
            for changed_file in event.changed_files:
                parent = Path(changed_file).parent
                dir_key = str(parent) if str(parent) not in {"", "."} else "."
                dir_counts[dir_key] = dir_counts.get(dir_key, 0) + 1
        top_changed_dirs = sorted(dir_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
        tree = working_tree_by_repo.get(repo_path)
        repo_summaries.append(
            RepoSummary(
                repo_path=repo_path,
                commit_count=len(events),
                top_changed_dirs=top_changed_dirs,
                branches=[tree.branch] if tree else [],
                last_commit_subject=events[0].subject if events else "",
            )
        )

    installed = [event for event in package_events if event.action == "installed"]
    removed = [event for event in package_events if event.action == "removed"]
    upgraded = [event for event in package_events if event.action == "upgraded"]

    return ActivitySummary(
        scanned_at=datetime.now(timezone.utc),
        since=datetime.now(timezone.utc),
        git=repo_summaries,
        packages=PackageSummary(installed=installed, removed=removed, upgraded=upgraded),
        working_trees=working_trees,
        system=system,
    )


def store_snapshot(activity_dir: Path, summary: ActivitySummary) -> Path:
    """Persist a JSONL snapshot of the activity summary."""
    snapshots_dir = activity_dir / "snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshots_dir / f"{summary.scanned_at.strftime('%Y-%m-%dT%H-%M-%S')}.jsonl"
    records = {
        "scanned_at": summary.scanned_at.isoformat(),
        "since": summary.since.isoformat(),
        "git": [
            {
                "repo_path": str(repo.repo_path),
                "commit_count": repo.commit_count,
                "top_changed_dirs": repo.top_changed_dirs,
                "branches": repo.branches,
                "last_commit_subject": repo.last_commit_subject,
            }
            for repo in summary.git
        ],
        "packages": {
            "installed": [
                {"package": event.package, "version": event.version, "timestamp": event.timestamp.isoformat()}
                for event in summary.packages.installed
            ],
            "removed": [
                {"package": event.package, "version": event.version, "timestamp": event.timestamp.isoformat()}
                for event in summary.packages.removed
            ],
            "upgraded": [
                {"package": event.package, "version": event.version, "timestamp": event.timestamp.isoformat()}
                for event in summary.packages.upgraded
            ],
        },
        "working_trees": [
            {
                "repo_path": str(tree.repo_path),
                "branch": tree.branch,
                "dirty": tree.dirty,
                "untracked": tree.untracked,
                "modified": tree.modified,
                "staged": tree.staged,
                "stash_count": tree.stash_count,
            }
            for tree in summary.working_trees
        ],
        "system": (
            {
                "uptime_seconds": summary.system.uptime_seconds,
                "load_1": summary.system.load_1,
                "load_5": summary.system.load_5,
                "load_15": summary.system.load_15,
                "mem_used_gb": summary.system.mem_used_gb,
                "mem_total_gb": summary.system.mem_total_gb,
                "disk_used_gb": summary.system.disk_used_gb,
                "disk_total_gb": summary.system.disk_total_gb,
            }
            if summary.system
            else None
        ),
    }
    snapshot_path.write_text(json.dumps(records) + "\n", encoding="utf-8")
    return snapshot_path


def save_last_session(activity_dir: Path, ts: datetime) -> Path:
    """Persist the last activity scan timestamp."""
    activity_dir.mkdir(parents=True, exist_ok=True)
    path = activity_dir / "last_session.json"
    path.write_text(json.dumps({"last_scan": ts.isoformat()}), encoding="utf-8")
    return path


def load_last_session(activity_dir: Path) -> datetime | None:
    """Load the last activity scan timestamp if present."""
    path = activity_dir / "last_session.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw = str(payload.get("last_scan", "")).strip()
        if not raw:
            return None
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def cleanup_old_snapshots(activity_dir: Path, retention_days: int) -> None:
    """Delete snapshots older than the retention window."""
    snapshots_dir = activity_dir / "snapshots"
    if not snapshots_dir.exists():
        return
    cutoff = datetime.now(timezone.utc).timestamp() - (max(1, int(retention_days)) * 24 * 60 * 60)
    for path in snapshots_dir.glob("*.jsonl"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


class CodeOnlySummarizer:
    """Template-based summarizer — no LLM."""

    def summarize(self, summary: ActivitySummary, token_budget: int) -> str:
        has_content = (
            bool(summary.git)
            or bool(summary.packages.installed)
            or bool(summary.packages.removed)
            or bool(summary.packages.upgraded)
            or bool(summary.working_trees)
            or summary.system is not None
        )
        if not has_content:
            return ""

        sections: list[str] = []
        char_budget = max(1, int(token_budget)) * 4

        for repo in summary.git:
            repo_name = repo.repo_path.name
            dirs_str = ", ".join(f"{name} {count}" for name, count in repo.top_changed_dirs[:3])
            branch_str = repo.branches[0] if repo.branches else "unknown"
            sections.append(
                f"Git: {repo_name}/ — {repo.commit_count} commits ({dirs_str}). Branch: {branch_str}."
            )

        pkg_parts: list[str] = []
        if summary.packages.installed:
            pkg_parts.append(
                "installed " + ", ".join(event.package for event in summary.packages.installed[:5])
            )
        if summary.packages.upgraded:
            pkg_parts.append(
                "upgraded "
                + ", ".join(f"{event.package} {event.version}" for event in summary.packages.upgraded[:5])
            )
        if summary.packages.removed:
            pkg_parts.append(
                "removed " + ", ".join(event.package for event in summary.packages.removed[:5])
            )
        if pkg_parts:
            sections.append(f"Packages: {'. '.join(pkg_parts)}.")

        for tree in summary.working_trees:
            if not tree.dirty:
                continue
            parts: list[str] = []
            if tree.modified:
                parts.append(f"{tree.modified} modified")
            if tree.staged:
                parts.append(f"{tree.staged} staged")
            if tree.untracked:
                parts.append(f"{tree.untracked} untracked")
            if tree.stash_count:
                parts.append(f"{tree.stash_count} stashes")
            if parts:
                sections.append(f"Working tree ({tree.repo_path.name}): {', '.join(parts)}.")

        if summary.system:
            system = summary.system
            days = int(system.uptime_seconds / 86400)
            uptime_str = f"{days}d" if days > 0 else f"{int(system.uptime_seconds / 3600)}h"
            sections.append(
                f"System: up {uptime_str}, load {system.load_1}, "
                f"mem {system.mem_used_gb}/{system.mem_total_gb}GB, "
                f"disk {int(system.disk_used_gb)}/{int(system.disk_total_gb)}GB."
            )

        header = f"[Recent Activity — since {summary.since.strftime('%Y-%m-%d %H:%M')}]"
        while sections:
            body = "\n".join(sections)
            full = f"{header}\n{body}"
            if len(full) <= char_budget:
                return full
            sections.pop()
        return ""


def build_injection_text(summary: ActivitySummary | None, token_budget: int = 200) -> str:
    """Build activity context text for prompt injection."""
    if summary is None:
        return ""
    return CodeOnlySummarizer().summarize(summary, token_budget)


def format_activity_report(summary: ActivitySummary | None) -> str:
    """Render an operator-facing activity report without injection truncation."""
    if summary is None:
        return ""

    lines = [f"[Recent Activity — since {summary.since.strftime('%Y-%m-%d %H:%M')}]"]
    for repo in summary.git:
        dirs = ", ".join(f"{name} {count}" for name, count in repo.top_changed_dirs[:5]) or "no changed dirs"
        branches = ", ".join(repo.branches) or "unknown"
        lines.append(
            f"Git: {repo.repo_path.name}/ — {repo.commit_count} commits | dirs: {dirs} | branches: {branches} | last: {repo.last_commit_subject}"
        )
    if summary.packages.installed or summary.packages.upgraded or summary.packages.removed:
        pkg_parts: list[str] = []
        if summary.packages.installed:
            pkg_parts.append(
                "installed "
                + ", ".join(f"{event.package} {event.version}" for event in summary.packages.installed)
            )
        if summary.packages.upgraded:
            pkg_parts.append(
                "upgraded "
                + ", ".join(f"{event.package} {event.version}" for event in summary.packages.upgraded)
            )
        if summary.packages.removed:
            pkg_parts.append(
                "removed "
                + ", ".join(f"{event.package} {event.version}" for event in summary.packages.removed)
            )
        lines.append("Packages: " + " | ".join(pkg_parts))
    for tree in summary.working_trees:
        lines.append(
            f"Working tree ({tree.repo_path.name}): branch={tree.branch} dirty={tree.dirty} modified={tree.modified} staged={tree.staged} untracked={tree.untracked} stashes={tree.stash_count}"
        )
    if summary.system:
        system = summary.system
        lines.append(
            f"System: uptime={int(system.uptime_seconds)}s load={system.load_1}/{system.load_5}/{system.load_15} "
            f"mem={system.mem_used_gb}/{system.mem_total_gb}GB disk={system.disk_used_gb}/{system.disk_total_gb}GB"
        )
    return "\n".join(lines)


def scan_and_store(
    config,
    activity_dir: Path,
    *,
    persist_last_session: bool = True,
) -> ActivitySummary | None:
    """Run collectors, aggregate, and store a summary."""
    if not getattr(config, "enabled", False):
        return None

    repo_paths = [
        Path(path).expanduser()
        for path in list(getattr(config, "repo_paths", []))[: max(1, int(getattr(config, "max_repos", 5)))]
    ]

    last_ts = load_last_session(activity_dir)
    since = last_ts if last_ts is not None else datetime.now(timezone.utc) - timedelta(hours=24)

    git_events = collect_git_activity(
        repo_paths,
        since,
        max_commits_per_repo=max(1, int(getattr(config, "max_commits_per_repo", 50))),
    )
    package_events = collect_pacman_activity(since)
    working_trees = collect_working_tree_summary(repo_paths)
    system = collect_system_stats()

    summary = aggregate_snapshot(git_events, package_events, working_trees, system)
    summary.since = since

    try:
        store_snapshot(activity_dir, summary)
        if persist_last_session:
            save_last_session(activity_dir, summary.scanned_at)
        cleanup_old_snapshots(
            activity_dir,
            retention_days=max(1, int(getattr(config, "retention_days", 30))),
        )
    except OSError:
        pass

    return summary
