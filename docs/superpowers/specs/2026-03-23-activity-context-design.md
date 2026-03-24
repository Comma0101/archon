# Lightweight Activity Context — Design Spec

**Goal:** Give Archon awareness of what changed between sessions — git commits, package operations, file changes, system state — so it can be immediately relevant when a conversation starts.

**Approach:** Reactive scan at session start. No daemon, no background process. Four code-only collectors produce structured data, stored as JSONL snapshots, injected into the system prompt when relevant.

**Scope:** V1 is Arch Linux–specific (pacman, `/proc`). Interfaces are clean enough to swap implementations later, but no abstraction layer ships now.

---

## 1. Problem

Archon starts every session with zero knowledge of what happened outside conversations. If the user installed packages, committed code, or rebooted since the last chat, Archon doesn't know. The user must re-explain context that the system could have inferred.

## 2. Goals

- Detect git activity, package changes, file change patterns, and system state between sessions.
- Inject relevant context into the system prompt at session start — no manual `/activity` required.
- Keep it cheap: no LLM calls for v1, <500ms scan time, <200 tokens injected.
- Fit Archon's existing patterns: dataclasses, dependency injection, XDG dirs, TOML config.

## 3. Non-Goals

- No real-time file watching or inotify.
- No background daemon or process management.
- No window focus tracking, browser history, or shell history.
- No LLM-based summarization in v1 (interface designed, implementation deferred).
- No proactive hints ("I see you installed X") — injection is passive context, not conversation.
- No writing activity data into canonical memory. Activity stays in its own store.

---

## 4. Data Sources & Collectors

Four collectors, each a pure function returning structured data. All synchronous, fast (<500ms total), no LLM, no network calls.

### 4.1 Git Activity

```python
def collect_git_activity(
    repo_paths: list[Path],
    since: datetime,
    max_commits_per_repo: int = 50,
) -> list[GitEvent]:
```

- Runs `git log --format='%H|%aI|%s' --since=<iso> --name-only` per repo.
- Returns: repo path, commit count, files changed (grouped by directory), branches active, last commit summary.
- Bounded: max 50 commits per repo, max 5 repos (from config).
- Catches `subprocess.CalledProcessError` and `FileNotFoundError` — returns empty list on failure.

### 4.2 Pacman Log

```python
def collect_pacman_activity(
    since: datetime,
    log_path: Path = Path("/var/log/pacman.log"),
) -> list[PackageEvent]:
```

- Parses `/var/log/pacman.log` backwards from EOF until `since` timestamp.
- Extracts: action (installed/removed/upgraded), package name, version, timestamp.
- Direct file read + regex — no subprocess.
- Returns empty list if file missing or unreadable.

### 4.3 File Change Stats

```python
def collect_file_changes(
    repo_paths: list[Path],
    since: datetime,
) -> list[FileChangeCluster]:
```

- Runs `git diff --stat HEAD@{<since>}` per repo.
- Falls back to `git log --name-only --since` if reflog doesn't reach far enough.
- Returns directory-level change counts (e.g., `archon/tools/: 12 files`).
- No recursive `find`, no inotify — git is the source of truth for tracked repos.

### 4.4 System Stats

```python
def collect_system_stats() -> SystemSnapshot:
```

- Reads `/proc/uptime`, `/proc/loadavg`, `/proc/meminfo` directly.
- Computes root disk usage via `shutil.disk_usage("/")`.
- Returns: uptime, load averages, memory used/total, disk used/total.
- Single point-in-time snapshot. No historical data.

---

## 5. Storage & Aggregation

### 5.1 Storage Layout

```
~/.local/state/archon/activity/
├── snapshots/
│   ├── 2026-03-23T14:30:00.jsonl
│   └── 2026-03-22T09:15:00.jsonl
└── last_session.json
```

- Each snapshot: one JSONL file, one line per collector result.
- `last_session.json`: timestamp of last scan + summary hash for dedup.
- Snapshots older than `retention_days` auto-deleted on next scan (glob + mtime check).

### 5.2 Aggregator

```python
def aggregate_snapshot(
    git_events: list[GitEvent],
    package_events: list[PackageEvent],
    file_changes: list[FileChangeCluster],
    system: SystemSnapshot,
) -> ActivitySummary:
```

Pure function. Produces:

```python
@dataclass
class ActivitySummary:
    scanned_at: datetime
    since: datetime
    git: list[RepoSummary]
    packages: PackageSummary
    file_changes: list[DirSummary]
    system: SystemSnapshot
```

- `RepoSummary`: repo path, commit count, top 5 changed directories, active branches, last commit message.
- `PackageSummary`: installed/removed/upgraded lists with package names and versions.
- `DirSummary`: repo path, directory → file count mapping.

### 5.3 Size Budget

Each snapshot: ~2–5KB. 30 days at 2 sessions/day ≈ 300KB max. Negligible.

---

## 6. Injection Strategy

### 6.1 Triggers

Activity context is NOT always injected. Three triggers:

| Trigger | When | What's injected |
|---------|------|-----------------|
| Session start after gap >1h | First turn of session, `now - last_session > gap_threshold` | Full summary |
| CWD matches tracked repo | Any turn where CWD is inside a configured `repo_paths` entry | That repo's activity only |
| Explicit `/activity` command | User requests it | Full detail, uncapped |

**No injection when:** Gap <1h, no activity detected, activity config disabled, CWD not in tracked repos.

### 6.2 Integration Point

`_build_turn_system_prompt()` in `agent.py`, after the memory prefetch block (around line 1323):

```python
activity_text = activity.build_injection_text(
    summary=self._activity_summary,
    cwd=Path.cwd(),
    trigger="session_start",  # or "cwd_match"
    token_budget=config.activity.token_budget,
)
if activity_text:
    parts.append(activity_text)
```

`self._activity_summary` is populated once at session start by calling `activity.scan_and_store()`.

### 6.3 Token Budget

Hard cap: 200 tokens (configurable). Output format:

```
[Recent Activity — since 2026-03-22 09:15]
Git: archon/ — 8 commits (tools.py 3, agent.py 2, tests/ 3). Branch: master.
Packages: installed python-httpx, python-pydantic. Updated linux 6.18.12→6.18.13.
System: up 3d, load 0.4, mem 8.2/32GB, disk 45%.
```

### 6.4 First-Turn Behavior

Session-start trigger fires on the first turn only, then is not repeated. CWD-match trigger can fire on subsequent turns but only when CWD changes between turns.

---

## 7. Summarizer Interface

### 7.1 Protocol

```python
class ActivitySummarizer(Protocol):
    def summarize(self, summary: ActivitySummary, token_budget: int) -> str: ...
```

### 7.2 V1 Implementation: CodeOnlySummarizer

Template-based string formatting. No LLM call. Prioritizes sections by content:
1. Git activity (highest signal)
2. Package changes
3. File change clusters (if space remains)
4. System stats (lowest priority, truncated first)

Truncation: if formatted text exceeds `token_budget`, drop system stats first, then file changes, then truncate package list to top 5.

### 7.3 Future: LLMSummarizer

Accepts same `ActivitySummary`, calls a small model (Haiku / local 7B) to extract meaning. Slots behind the same protocol. Configured via `summarizer = "llm"` in config. Not implemented in v1.

---

## 8. Configuration

New `[activity]` section in `~/.config/archon/config.toml`:

```toml
[activity]
enabled = false
repo_paths = []
gap_threshold_minutes = 60
token_budget = 200
retention_days = 30
summarizer = "code"
max_repos = 5
max_commits_per_repo = 50
```

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

**Defaults are conservative:** disabled, no repos. User must opt in.

---

## 9. CLI Commands

Three commands under `archon activity`:

| Command | Effect |
|---------|--------|
| `archon activity status` | Show config, last scan time, tracked repos, whether injection is active |
| `archon activity summary` | Run collectors now, show full formatted summary (uncapped) |
| `archon activity reset` | Delete all snapshots, reset `last_session.json` |

Implementations in `archon/cli_activity_commands.py` following the existing DI pattern.

---

## 10. File Structure

### New Files

| File | Responsibility | ~Lines |
|------|---------------|--------|
| `archon/activity.py` | Collectors, dataclasses, aggregator, summarizer, store, injector | ~300 |
| `archon/cli_activity_commands.py` | CLI command implementations with DI | ~80 |
| `tests/test_activity.py` | Unit tests for collectors, aggregator, summarizer, injection | ~250 |
| `tests/test_cli_activity_commands.py` | CLI command tests | ~60 |

### Modified Files

| File | Change |
|------|--------|
| `archon/config.py` | Add `ActivityConfig` dataclass, parse `[activity]` in `load_config()` |
| `archon/cli.py` | Add `@main.group("activity")` with subcommands, wire DI |
| `archon/agent.py` | Call `activity.scan_and_store()` at session init, call `activity.build_injection_text()` in `_build_turn_system_prompt()` |

### Dependency Direction

```
agent.py → activity.py → config.py
cli.py → cli_activity_commands.py → activity.py → config.py
```

Activity module does not import agent.

---

## 11. Error Handling

**General principle:** Activity context is best-effort enrichment. Every failure degrades to "no context injected" — never blocks the session.

| Failure | Behavior |
|---------|----------|
| Git not installed / repo path invalid | Collector returns empty list, debug log |
| Pacman log missing or unreadable | Collector returns empty list, debug log |
| `/proc` files unreadable | System stats returns None, skipped in summary |
| All collectors return empty | No injection. Clean no-op. |
| `last_session.json` corrupted | Treat as first run, scan with `since=24h ago` |
| JSONL snapshot corrupted | Skip it, scan fresh |
| Config repo path doesn't exist | Skip it, warn in debug log |
| Scan takes >2s (shouldn't happen) | No timeout — still faster than first LLM call |

---

## 12. Testing Strategy

### Unit Tests (no filesystem, no subprocess)

- **Collectors:** Mock `subprocess.run` return values and file reads. Verify parsing of known git log formats, pacman log formats. Test boundary cases (empty log, malformed entries, >50 commits).
- **Aggregator:** Pass known event lists, assert `ActivitySummary` field values. Test empty inputs, single-source inputs, multi-repo inputs.
- **Summarizer:** Pass known `ActivitySummary`, assert output within token budget. Test truncation priority (system stats dropped first, then file changes).
- **Injector:** Test all three triggers. Verify gap <1h produces empty string. Verify CWD match injects only matching repo. Verify disabled config produces empty string.
- **Config:** Verify `ActivityConfig` defaults. Verify TOML round-trip.

### Integration Tests (temp dirs)

- Create temp git repo with commits, verify `collect_git_activity` parsing.
- Write fake pacman log to temp file, verify `collect_pacman_activity` parsing.
- Write/read snapshots to temp state dir, verify JSONL round-trip.
- Verify `last_session.json` timestamp tracking across two scans.

### CLI Tests

- `archon activity status` with disabled config → shows "disabled".
- `archon activity status` with enabled config → shows repos and last scan.
- `archon activity summary` with no snapshots → shows "no activity recorded".
- `archon activity reset` → state dir cleared.

---

## 13. Acceptance Criteria

1. `archon activity status` shows config and last scan time.
2. `archon activity summary` runs all collectors and displays formatted output.
3. `archon activity reset` clears all snapshot data.
4. Git collector parses `git log` output correctly for 1–50 commits.
5. Git collector handles missing repo, missing git, empty log gracefully.
6. Pacman collector parses install/remove/upgrade entries correctly.
7. Pacman collector handles missing log file gracefully.
8. File change collector produces directory-level summaries from git diff.
9. System stats reads /proc correctly on Arch Linux.
10. System stats returns None gracefully on non-Linux.
11. Aggregator produces correct `ActivitySummary` from mixed collector outputs.
12. Aggregator handles all-empty inputs (produces empty summary).
13. `CodeOnlySummarizer` output fits within 200-token budget.
14. `CodeOnlySummarizer` truncates lower-priority sections first.
15. Injection fires on session start when gap >1h.
16. Injection does NOT fire when gap <1h.
17. Injection fires for CWD-matching repo on any turn.
18. Injection does NOT fire when CWD is outside all tracked repos (non-session-start).
19. Injection produces empty string when activity is disabled.
20. `/activity` command shows full uncapped summary.
21. Snapshots written as valid JSONL, readable on next session.
22. `last_session.json` updated after each scan.
23. Snapshots older than `retention_days` cleaned up on scan.
24. `ActivityConfig` parsed correctly from TOML with all defaults.
25. Collector failures (subprocess errors, file errors) don't propagate — empty results only.
26. No new dependencies added (stdlib only: subprocess, datetime, json, shutil, re, dataclasses).
27. All tests pass with no real git repos, no real pacman log, no real /proc reads.

---

## 14. Rollout

Single slice. The feature is behind `enabled = false` by default. Ship it all, let users opt in via config.

No phased rollout needed — the feature is self-contained, has no effect when disabled, and touches only three existing files with minimal changes (config parsing, CLI registration, one injection call in agent).
