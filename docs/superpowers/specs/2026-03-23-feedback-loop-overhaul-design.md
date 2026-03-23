# Feedback Loop Overhaul — Design Spec

**Date**: 2026-03-23
**Status**: Approved
**Approach**: Hybrid (streaming callback for shell, post-hoc enrichment for all others)

## Problem

Archon's tool execution is a black box. The operator sees a spinner label, then silence, then the model's response. There is no indication of what state changed, no incremental output for long-running commands, no diff for file edits, and no visible distinction between blocked/completed/failed states. CLI and Telegram both suffer equally.

## Goals

1. Tool start is visible immediately
2. Long-running tool execution shows progress or output incrementally
3. File edits show a compact diff/result summary
4. Completion/failure state is obvious
5. CLI and Telegram share the same state vocabulary

## Non-Goals

- No TUI or rich/curses dependency
- No major agent-loop rewrite
- No new approval mechanism (existing flow stays)
- No model-facing tool contract changes (return strings to the model unchanged)
- No IDE integration or growth dashboard work

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Shell streaming | Line-by-line via Popen | Biggest single UX win; shell is the only tool with unpredictable duration and meaningful real-time output |
| Diff format | Unified diff, truncated | Familiar to developers, renders in monospace (CLI) and code blocks (Telegram) |
| Event structure | Structured data, thin per-surface renderers | Each surface can format optimally without surface-specific logic in tools |
| Default verbosity | Adaptive — fast (<2s) is quiet, slow is loud | Balances noise vs. feedback; shell always streams regardless |

## Plumbing: How Handlers Communicate Events and Metadata

Tool handlers today are isolated closures that return a string. They have no outbound channel for mid-execution events or structured metadata. This section specifies how to bridge that gap without changing what the model sees.

### ToolContext — a lightweight execution context

`ToolRegistry.execute()` creates a `ToolContext` object before calling the handler and passes it as an optional `_ctx` keyword argument. Handlers that want to emit events or return metadata opt in by accepting `_ctx`. Handlers that don't accept it continue to work unchanged.

```python
@dataclass
class ToolContext:
    tool_name: str
    emit: Callable[[UXEvent], None]    # emit mid-execution events
    meta: dict[str, Any]               # structured metadata (handler writes, caller reads)
```

**How it flows:**

1. `ToolRegistry.execute()` creates `ToolContext(tool_name=name, emit=self._emit_ux_event, meta={})`
2. If the handler's signature accepts `_ctx`, pass it. Otherwise, don't.
3. Handler optionally calls `_ctx.emit(tool_running(...))` during execution.
4. Handler optionally writes to `_ctx.meta` (e.g., `_ctx.meta["exit_code"] = rc`).
5. After handler returns, `ToolRegistry.execute()` reads `_ctx.meta` to build the enriched `tool_end` summary.
6. If `_ctx.meta` is empty (handler didn't opt in), fall back to parsing the return string for summary generation.

**What this means per tool:**

| Tool | Accepts `_ctx`? | Emits during execution? | Writes metadata? |
|------|----------------|------------------------|-----------------|
| `shell` | Yes (Slice 2) | `tool_running(output_line)` per line | `exit_code`, `line_count` |
| `edit_file` | Yes (Slice 3) | `tool_diff` after edit | `path`, `line_number`, `lines_changed`, `diff_lines` |
| `read_file` | Yes (Slice 1) | No | `path`, `line_count` |
| `write_file` | Yes (Slice 1) | No | `path`, `line_count`, `is_new` |
| `grep` | Yes (Slice 1) | No | `pattern`, `match_count`, `file_count` |
| `glob` | Yes (Slice 1) | No | `pattern`, `file_count` |
| All others | No (initially) | No | No — fallback to return-string parsing |

**Why `_ctx` and not a global or closure capture**: The handler closures already close over `registry`, so they _could_ access an emitter via `registry`. But that creates invisible coupling. An explicit `_ctx` parameter is inspectable, testable, and grep-able. It also makes clear which handlers have opted in.

### Detecting blocked actions

The confirmer functions (`confirm_for_terminal_session`, Telegram equivalent) currently return `False` to reject. The handler then returns a rejection string. `ToolRegistry.execute()` cannot distinguish this from a normal return.

**Solution**: Handlers that go through the safety gate write `_ctx.meta["blocked"] = True` and `_ctx.meta["command_preview"] = cmd` when the confirmer returns `False`. `ToolRegistry.execute()` checks `_ctx.meta.get("blocked")` after the handler returns and emits `tool_blocked` instead of `tool_end(completed)`.

This does not change the approval mechanism. The confirmer still returns bool. The pending request state machine still works. The handler still returns a rejection string to the model. The only new behavior is that `ToolRegistry.execute()` now knows _why_ the handler returned that string.

### Summary generation

`ToolRegistry.execute()` builds the `tool_end` summary from `_ctx.meta` when available. A `build_tool_summary(tool_name, meta, result_str)` function handles this:

- If `meta` has the needed fields (e.g., `exit_code` + `line_count` for shell), use them directly.
- If `meta` is empty (handler didn't opt in), parse the return string as a fallback. This is intentionally fragile — it only needs to work for the current return string formats, and it degrades to `"{tool_name}: done"` if parsing fails.

This keeps the spec honest: structured metadata is preferred, string parsing is a transitional fallback.

## Progress State Model

Every tool execution moves through exactly one of these states:

| State | Meaning | When |
|-------|---------|------|
| `started` | Tool handler has been called | Immediately on entry |
| `running` | Tool is producing output or still working | Shell lines arriving, or elapsed >2s |
| `blocked` | Waiting for operator approval | Safety gate returned DANGEROUS |
| `completed` | Tool returned a result | Handler returned normally |
| `failed` | Tool raised an exception or timed out | Exception caught or timeout |

**Rules:**
- Every execution emits exactly one `started` and exactly one terminal state (`completed`, `failed`, or `blocked`).
- `running` events are optional — only emitted when there's incremental output (shell lines) or when the 2s adaptive threshold is crossed.
- `blocked` is terminal **for that tool execution**. The tool returns "Command rejected by safety gate." and the approval flow takes over as today. If the operator approves, the existing replay mechanism re-runs `agent.run()` with the original user input — the model may re-issue the same tool call, which starts a **new** execution with its own `started` -> `completed` lifecycle. The blocked and approved executions are two separate event sequences, not one continuous flow.
- Adaptive threshold: tools completing in <2s show only the `completed` summary. Tools exceeding 2s show a heartbeat. Shell is exempt — always streams from the start.

## UXEvent Additions

### New event kinds

**`tool_running`** — incremental progress or heartbeat
```python
{
    "kind": "tool_running",
    "tool": "shell",
    "detail_type": "output_line" | "heartbeat",
    # for output_line:
    "line": "Resolving dependencies...",
    # for heartbeat:
    "elapsed_s": 4.2,
}
```

**`tool_blocked`** — dangerous action awaiting approval
```python
{
    "kind": "tool_blocked",
    "tool": "shell",
    "command_preview": "pacman -Syu",
    "safety_level": "DANGEROUS",
}
```

**`tool_diff`** — file edit result summary
```python
{
    "kind": "tool_diff",
    "tool": "edit_file",
    "path": "archon/agent.py",
    "diff_lines": [
        "-    max_iter = 15",
        "+    max_iter = 30",
    ],
    "context_before": "    def run(self):",
    "lines_changed": 1,
}
```

### Enriched `tool_end`

The existing `tool_end` gains a `summary` field:

| Tool | Summary format |
|------|---------------|
| `shell` | `"shell: exit 0 (14 lines)"` or `"shell: exit 1 (error)"` |
| `read_file` | `"read: archon/agent.py (142 lines)"` |
| `edit_file` | `"edit: archon/agent.py:42 (1 line changed)"` |
| `write_file` | `"write: archon/config.py (new, 38 lines)"` |
| `grep` | `"grep: 'max_iter' -> 3 matches in 2 files"` |
| `glob` | `"glob: *.py -> 47 files"` |
| `web_search` | `"search: 'archlinux kernel' -> 8 results"` |

### Unchanged events

`tool_start` gains structured `tool` and `args_summary` fields (backward-compatible — `render_text()` still works). `iteration_progress`, `compaction_triggered`, `job_progress`, `job_completed` unchanged.

## Per-Surface Rendering

### CLI Renderer

Plain ANSI on stderr, consistent with current spinner placement:

| Event | Rendering |
|-------|-----------|
| `tool_start` (fast, <2s, non-shell) | Nothing — wait for completion |
| `tool_start` (>2s elapses, non-shell) | Spinner continues with phase label |
| `tool_start` (shell) | Spinner immediately; streaming begins on first output line |
| `tool_running` (output_line) | Print dim: `│ Resolving dependencies...` |
| `tool_running` (heartbeat) | Update spinner: `⠹ running command (4s)` |
| `tool_blocked` | Stop spinner: `⚠ blocked: pacman -Syu (DANGEROUS) — /approve or /deny` |
| `tool_end` (completed) | Stop spinner: `✓ shell: exit 0 (14 lines)` |
| `tool_end` (failed) | Stop spinner, red: `✗ shell: exit 1 (error)` |
| `tool_diff` | Indented under tool_end: red `-` lines, green `+` lines |

Shell streaming: lines prefixed with `│ ` in dim. >20 lines collapsed to first 8 + `│ ... (N more lines)` + last 5.

Diff display: max 10 lines. Longer diffs: `  ... (N more lines changed)`. No diff for failed edits.

### Telegram Renderer

| Event | Rendering |
|-------|-----------|
| `tool_start` (fast, <2s, non-shell) | Nothing |
| `tool_running` (output_line) | Batched — collect lines, send as code block every 3s |
| `tool_running` (heartbeat) | Edit previous message in-place |
| `tool_blocked` | Message with inline approve/deny buttons |
| `tool_end` | `✓ shell: exit 0 (14 lines)` |
| `tool_end` (failed) | `✗ shell: exit 1 (error)` |
| `tool_diff` | Code block with `diff` language tag |

Same collapse thresholds as CLI. Heartbeats edit previous message rather than sending new ones.

### Shared Logic

Both renderers call shared functions for:
1. Adaptive threshold decision (show start immediately or wait)
2. Summary string generation from structured data
3. Diff line truncation
4. Shell output collapse (middle-elision pattern)

Renderers are format-and-emit only — ~50 lines each.

## Operator Journeys

### Shell — fast (`ls -la`)

```
user> list the files in /etc/pacman.d
✓ shell: exit 0 (8 lines)
Archon: Here are the files in /etc/pacman.d: ...
```

### Shell — slow (`makepkg -si`)

```
user> build and install this PKGBUILD
⠹ running command
│ ==> Making package: my-pkg 1.0-1
│ ==> Checking runtime dependencies...
│ ==> Retrieving sources...
│ ... (28 more lines)
│ ==> Finished making: my-pkg 1.0-1
✓ shell: exit 0 (36 lines)
Archon: The package was built and installed.
```

### read_file

```
user> what's in my pacman config?
✓ read: /etc/pacman.conf (74 lines)
Archon: Your pacman.conf contains...
```

### edit_file

```
user> bump max_iter to 30
✓ edit: archon/agent.py:42 (1 line changed)
  -    max_iter = 15
  +    max_iter = 30
Archon: Done, I've updated max_iter to 30.
```

### Blocked dangerous action

```
user> update my system
⚠ blocked: pacman -Syu (DANGEROUS) — /approve or /deny
```
After `/approve` (replays user input — new agent turn, new tool execution):
```
⠹ running command
│ :: Synchronizing package databases...
│ :: Starting full system upgrade...
│ ... (12 more lines)
✓ shell: exit 0 (18 lines)
Archon: System updated. 3 packages upgraded.
```

## Acceptance Criteria

### Core
1. Every tool execution emits `tool_start` and exactly one terminal event (`completed`, `failed`, or `blocked`)
2. Shell tool streams stdout/stderr line-by-line via `tool_running(output_line)` events
3. `edit_file` success emits `tool_diff` with unified diff lines, path, and line number
4. `tool_end` carries structured summary per tool type
5. `tool_blocked` event emitted when DANGEROUS command is rejected
6. Adaptive threshold: <2s tools show only `tool_end` summary; >2s tools show heartbeat/streaming
7. CLI and Telegram render the same events through thin renderers

### Shell Streaming
8. Shell uses `Popen` with line-by-line reads instead of `subprocess.run(capture_output=True)`
9. Output collapse: >20 lines shown as first 8 + elision + last 5
10. Timeout behavior unchanged (default 30s, configurable)
11. Full output still returned to model as single string

### Diff Display
12. Unified diff format with 1 line of context above
13. Max 10 diff lines displayed; longer diffs show elision with count
14. CLI: `-` red, `+` green, context dim. Telegram: ```diff code blocks
15. Failed edits show `tool_end(failed)` with error — no diff attempted

### Rendering
16. CLI: all feedback on stderr, consistent with current spinner
17. CLI: streaming lines prefixed with `│ ` in dim
18. Telegram: shell output batched every 3s into single code block
19. Telegram: heartbeats edit previous message in-place
20. Telegram: blocked actions show inline approve/deny buttons (existing mechanism)

### Non-regression
21. Model-facing tool return strings unchanged
22. Approval flow unchanged — `tool_blocked` is visibility, not mechanism
23. Permission modes (`confirm_all`, `accept_reads`, `auto`) unchanged
24. Safety classification logic untouched
25. Existing tests pass without modification

### Testability
26. New event kinds covered by unit tests
27. Shell streaming testable with mock subprocess
28. Diff generation testable with known old/new strings
29. Each renderer testable independently

## Rollout Order

### Slice 1: Structured events + enriched `tool_end`

Add new `UXEvent` kinds. Add structured fields to `tool_start`/`tool_end`. Add summary generation per tool type. Wire enrichment into `ToolRegistry.execute()`. Both renderers emit summaries.

Tools stay untouched. Shell still buffered. No diffs. No streaming.

**Operator sees**: `✓ shell: exit 0 (14 lines)` instead of spinner-then-silence. `⚠ blocked: ...` instead of model saying "rejected by safety gate."

### Slice 2: Shell streaming

Replace `subprocess.run(capture_output=True)` with `Popen` + line-by-line reads. Emit `tool_running(output_line)` per line. Add output collapse. Shell streams from start; other tools get heartbeat after 2s.

**Depends on**: Slice 1 (event infrastructure + renderers).

### Slice 3: Edit diffs

Compute unified diff on `edit_file` success. `edit_file` handler captures file content before the `str.replace()` call, computes diff via `difflib.unified_diff`, and emits `tool_diff` via `_ctx.emit()`. Writes diff metadata to `_ctx.meta`. CLI renders colored diff. Telegram renders ```diff code block. Collapse for >10 lines. Skip diff if file >50KB.

**Depends on**: Slice 1. **Independent of**: Slice 2.

### Slice 4: Telegram batching + heartbeat polish

Telegram-specific: batch shell output lines every 3s into single code block message. Heartbeat message editing in-place. Edge case cleanup.

**Depends on**: Slices 1-3.

```
Slice 1 (events + summaries)
  ├── Slice 2 (shell streaming) ──┐
  └── Slice 3 (edit diffs) ───────┤
                                  └── Slice 4 (telegram batching)
```

Slice 4 depends on both Slice 2 and Slice 3 — it polishes Telegram rendering for shell output (from Slice 2) and diffs (from Slice 3).

## Risks and What to Avoid

**Shell Popen complexity**: Use `selectors` or threading to read stdout/stderr concurrently. Don't use `communicate()`. Test with: no output, mixed stdout/stderr, timeout, rapid output.

**Telegram rate limits**: Never send per-line. 3s batching window. 500 lines in 1 second = one message, not 500.

**Diff on huge files**: Cap at 50KB. Skip diff computation, show summary only.

**Event ordering**: Drain Popen read loop completely before emitting `tool_end`. No output lines after completion.

**Don't change model-facing contract**: Enriched events are operator-only. Tool return strings to model unchanged.

**Don't over-render**: Fast is quiet, slow is loud. Five `read_file` calls in 0.3s = five quiet one-liners, not five spinners.

**Don't build a rendering framework**: ~50 lines per renderer. Plain ANSI for CLI, Markdown for Telegram. No layout engine.

**Don't touch approval mechanism**: `tool_blocked` is visibility. Confirmer, pending requests, `/approve`/`/deny` — all unchanged.
