# Lightweight Activity Context — Design Spec

**Goal:** Give Archon awareness of what changed between sessions — git commits, package operations, file changes, system state — so it can be immediately relevant when a conversation starts.

**Approach:** Reactive scan at session start. No daemon, no background process. Four code-only collectors produce structured data, stored as JSONL snapshots, injected into the system prompt when relevant.

**Scope:** V1 is Arch Linux–specific (pacman, `/proc`). Interfaces are clean enough to swap implementations later, but no abstraction layer ships now.

---

## 1. Problem

Archon starts every session with zero knowledge of what happened outside conversations. If the user installed packages, committed code, or rebooted since the last chat, Archon doesn't know. The user must re-explain context that the system could have inferred.

## 2. Goals

- Detect git activity, package changes, working tree state, and system state between sessions.
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

### 4.3 Working Tree Summary

```python
def collect_working_tree_summary(
    repo_paths: list[Path],
) -> list[WorkingTreeSummary]:
```

- Runs `git status --porcelain` and `git stash list` per repo.
- Returns: count of untracked/modified/staged files, stash count, current branch, dirty flag.
- This is a point-in-time snapshot of the working tree — not historical. It answers "what does the repo look like right now?" rather than duplicating the commit history from 4.1.
- Complements git activity (4.1 = what was committed since last session, 4.3 = what's uncommitted right now).

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
    working_trees: list[WorkingTreeSummary],
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
    working_trees: list[WorkingTreeSummary]
    system: SystemSnapshot
```

- `RepoSummary`: repo path, commit count, top 5 changed directories, active branches, last commit message.
- `PackageSummary`: installed/removed/upgraded lists with package names and versions.
- `WorkingTreeSummary`: repo path, branch, dirty flag, untracked/modified/staged counts, stash count.

### 5.3 Size Budget

Each snapshot: ~2–5KB. 30 days at 2 sessions/day ≈ 300KB max. Negligible.

---

## 6. Injection Strategy

### 6.1 Session Lifecycle

Activity scans are tied to **session boundaries**, not `Agent.__init__()`. A "new session" in Archon occurs at:

- **CLI**: `/reset` in the REPL (`cli_interactive_commands.py:478`), which calls `agent.reset()` and reassigns `session_id`.
- **CLI**: `/new` (fresh chat context), which also resets and reassigns.
- **Telegram**: `/reset` command (`telegram.py:373`), which pops and recreates the agent.
- **Telegram**: First message from a new chat (`telegram.py:608`), which creates a fresh agent via `_get_or_create_chat_agent()`.

The scan function `activity.scan_and_store()` is called by the **surface layer** (CLI REPL or Telegram adapter) at these session-boundary points — not by Agent itself. The surface passes the resulting `ActivitySummary` (or `None`) to the Agent, which stores it as `self._activity_summary` for prompt injection.

This means:
- CLI calls `scan_and_store()` at REPL startup and after `/reset`/`/new`.
- Telegram calls `scan_and_store()` when creating a new chat agent.
- Agent receives the summary as data — it never calls collectors directly.

### 6.2 Triggers

Activity context is NOT always injected. Two automatic triggers plus one explicit:

| Trigger | When | What's injected | Surface |
|---------|------|-----------------|---------|
| Session start after gap >1h | First turn, `now - last_session > gap_threshold` | Full summary | CLI + Telegram |
| Repo-context match | First turn, when a `repo_path` from config matches the scan | That repo's activity only | CLI + Telegram |
| Explicit `/activity` slash command | User requests it | Full detail, uncapped | CLI + Telegram |

**Dropped: CWD-match trigger.** The original design used `Path.cwd()` to detect repo context, but this is meaningless in Telegram (CWD is the bot's process dir, not the user's). Instead, repo-context matching uses the configured `repo_paths` directly — all tracked repos are included in the session-start scan, and the summarizer formats them.

**No injection when:** Gap <1h, no activity detected, activity config disabled, no `repo_paths` configured.

### 6.3 Integration Point

`_build_turn_system_prompt()` in `agent.py`. The activity text is appended to `lines` **before** the memory prefetch block, not after it. This avoids the early return at line 1306 (`if not prefetched: return`) which would silently drop activity context on turns with no matching memories.

```python
# After compaction lines, before memory prefetch:
activity_text = activity.build_injection_text(
    summary=self._activity_summary,
    token_budget=config.activity.token_budget,
)
if activity_text:
    lines.extend(["", activity_text])

# Then the existing memory prefetch block follows...
try:
    prefetched = memory_store.prefetch_for_query(user_message)
except Exception:
    prefetched = []
if not prefetched:
    return "\n".join(lines)  # activity context is already in lines
```

The `_activity_summary` is set by the surface layer at session start and cleared after first injection (session-start trigger fires once per session).

### 6.4 Token Budget

Hard cap: 200 tokens (configurable). Output format:

```
[Recent Activity — since 2026-03-22 09:15]
Git: archon/ — 8 commits (tools.py 3, agent.py 2, tests/ 3). Branch: master. Working tree: 3 modified, 1 untracked.
Packages: installed python-httpx, python-pydantic. Updated linux 6.18.12→6.18.13.
System: up 3d, load 0.4, mem 8.2/32GB, disk 45%.
```

### 6.5 First-Turn Behavior

Session-start trigger fires on the first turn only, then `_activity_summary` is set to `None` so it is not repeated on subsequent turns within the same session.

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
3. Working tree state (if space remains)
4. System stats (lowest priority, truncated first)

Truncation: if formatted text exceeds `token_budget`, drop system stats first, then working tree, then truncate package list to top 5.

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

## 9. Command Surfaces

### 9.1 CLI Subcommands

Three commands under `archon activity`:

| Command | Effect |
|---------|--------|
| `archon activity status` | Show config, last scan time, tracked repos, whether injection is active |
| `archon activity summary` | Run collectors now, show full formatted summary (uncapped) |
| `archon activity reset` | Delete all snapshots, reset `last_session.json` |

Implementations in `archon/cli_activity_commands.py` following the existing DI pattern.

### 9.2 Slash Command

`/activity` is registered in the REPL slash command palette (`cli_commands.py:SLASH_COMMAND_GROUPS`) and in the Telegram command handler. It runs all collectors and displays the full uncapped summary inline — same output as `archon activity summary` but within a chat session.

Implementation: `handle_repl_command()` in `cli_repl_commands.py` gains an `"activity"` branch that calls `activity.scan_and_summarize()` and returns the formatted text. Telegram adapter gains a matching `/activity` branch in its command handler.

This is the same function used by the CLI subcommand — one implementation, two surfaces.

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
| `archon/agent.py` | Add `_activity_summary` attribute, call `activity.build_injection_text()` in `_build_turn_system_prompt()` before memory prefetch |
| `archon/cli_interactive_commands.py` | Call `activity.scan_and_store()` at REPL start and after `/reset`/`/new`, pass summary to agent |
| `archon/cli_repl_commands.py` | Add `/activity` branch in `handle_repl_command()` |
| `archon/cli_commands.py` | Add `/activity` entry to `SLASH_COMMAND_GROUPS` |
| `archon/adapters/telegram.py` | Call `activity.scan_and_store()` in `_get_or_create_chat_agent()`, add `/activity` command handler |

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

- **Collectors:** Mock `subprocess.run` return values and file reads. Verify parsing of known git log, git status, pacman log formats. Test boundary cases (empty log, malformed entries, >50 commits).
- **Aggregator:** Pass known event lists, assert `ActivitySummary` field values. Test empty inputs, single-source inputs, multi-repo inputs.
- **Summarizer:** Pass known `ActivitySummary`, assert output within token budget. Test truncation priority (system stats dropped first, then working tree, then packages).
- **Injector:** Test session-start trigger (gap >1h vs <1h). Verify first-turn-only behavior (second call returns empty). Verify disabled config produces empty string. Verify activity text survives the memory-prefetch early return path.
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
8. Working tree collector parses `git status --porcelain` and `git stash list` correctly.
9. Working tree collector handles missing repo gracefully.
10. System stats reads /proc correctly on Arch Linux.
11. System stats returns None gracefully on non-Linux.
12. Aggregator produces correct `ActivitySummary` from mixed collector outputs.
13. Aggregator handles all-empty inputs (produces empty summary).
14. `CodeOnlySummarizer` output fits within 200-token budget.
15. `CodeOnlySummarizer` truncates lower-priority sections first.
16. Injection fires on session start when gap >1h.
17. Injection does NOT fire when gap <1h.
18. Injection fires on first turn only, not repeated within same session.
19. Injection produces empty string when activity is disabled.
20. Activity context survives the early-return path in `_build_turn_system_prompt()` (injected before memory prefetch, not after).
21. `/activity` slash command shows full uncapped summary in CLI REPL.
22. `/activity` slash command shows full uncapped summary in Telegram.
23. `archon activity summary` CLI subcommand produces same output as `/activity`.
24. Scan is triggered by surface layer (CLI REPL / Telegram), not by Agent.__init__().
25. CLI scan fires at REPL start and after `/reset` and `/new`.
26. Telegram scan fires when creating a new chat agent.
27. Snapshots written as valid JSONL, readable on next session.
28. `last_session.json` updated after each scan.
29. Snapshots older than `retention_days` cleaned up on scan.
30. `ActivityConfig` parsed correctly from TOML with all defaults.
31. Collector failures (subprocess errors, file errors) don't propagate — empty results only.
32. No new dependencies added (stdlib only: subprocess, datetime, json, shutil, re, dataclasses).
33. All tests pass with no real git repos, no real pacman log, no real /proc reads.

---

## 14. Rollout

Single slice. The feature is behind `enabled = false` by default. Ship it all, let users opt in via config.

No phased rollout needed — the feature is self-contained and has no effect when disabled. Modified files: config parsing (`config.py`), CLI subcommand registration (`cli.py`), prompt injection (`agent.py`), scan triggering at session boundaries (`cli_interactive_commands.py`, `adapters/telegram.py`), slash command registration (`cli_commands.py`, `cli_repl_commands.py`), and Telegram command handler (`adapters/telegram.py`).
