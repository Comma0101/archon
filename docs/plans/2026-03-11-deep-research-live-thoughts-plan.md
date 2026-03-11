# Deep Research Live Thoughts Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Surface live Deep Research thought summaries through the existing activity feed and make stream-backed job liveness truthful across processes.

**Architecture:** Keep the current streaming-first model. Reuse `ux.job_progress` for live thought notices, and replace false cross-process `stream inactive` failures with a nonterminal detached stream state that is reflected in `/job` formatting.

**Tech Stack:** Python, pytest, HookBus UX events, Deep Research stream persistence in `archon/research/store.py`

---

Execution notes:

- Use `@superpowers:test-driven-development` for each code change.
- If behavior differs from the design while implementing, stop and use `@superpowers:systematic-debugging` before changing scope.

### Task 1: Emit live thought-summary progress events

**Files:**
- Modify: `tests/test_research.py`
- Modify: `archon/research/store.py`

**Step 1: Write the failing tests**

Add focused tests in `tests/test_research.py` that:

- create a stream-backed research job
- register a `HookBus` listener for `ux.job_progress`
- call `consume_research_stream(...)` with a `content.delta` `thought_summary`
- assert that one progress event is emitted and its rendered text contains `research:<id>` and the thought text

Add a second test that feeds the same `thought_summary` twice and asserts only one progress event is emitted.

Example test shape:

```python
events = []
hook_bus = HookBus()
hook_bus.register("ux.job_progress", lambda event: events.append(event))

record = research_store.consume_research_stream(
    "abc",
    [
        SimpleNamespace(
            event_type="content.delta",
            event_id="evt-1",
            text="Checking sources",
            delta_type="thought_summary",
            status="in_progress",
        )
    ],
    hook_bus=hook_bus,
    mark_unfinished_as_error=False,
)

assert record is not None
assert len(events) == 1
assert "Checking sources" in events[0].payload["event"].render_text()
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_research.py -q -k 'consume_research_stream and thought_summary'`

Expected: FAIL because `consume_research_stream(...)` persists the summary but does not emit a progress event.

**Step 3: Write minimal implementation**

In `archon/research/store.py`, emit `_emit_job_progress_event(...)` only when the incoming `thought_summary` text is non-empty and changed from `latest.latest_thought_summary`.

Implementation shape:

```python
if delta_type == "thought_summary" and text and text != latest.latest_thought_summary:
    _emit_job_progress_event(
        job_kind="research",
        job_id=f"research:{latest.interaction_id}",
        status=status or latest.status or "in_progress",
        summary=text,
        hook_bus=hook_bus,
    )
```

Keep the existing persistence behavior unchanged apart from the new event emission and dedupe guard.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_research.py -q -k 'consume_research_stream and thought_summary'`

Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_research.py archon/research/store.py
git commit -m "feat: emit live deep research thought summaries"
```

### Task 2: Replace false `stream inactive` failures with detached state

**Files:**
- Modify: `tests/test_research_store.py`
- Modify: `archon/research/store.py`

**Step 1: Write the failing tests**

Add tests in `tests/test_research_store.py` that prove:

- reconciling a nonterminal stream-backed job with no live in-process monitor does not turn it into terminal `error`
- the reconciled record is marked with `stream_status == "stream.detached"`
- `ensure_research_recovery_started(...)` leaves a nonterminal job with missing `last_event_id` detached instead of rewriting it to `Research recovery unavailable`

Example assertion shape:

```python
reloaded = load_research_job("resume-456")
assert reloaded is not None
assert reloaded.status == "in_progress"
assert reloaded.stream_status == "stream.detached"
assert reloaded.summary == "Research in progress"
assert reloaded.error == ""
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_research_store.py -q -k 'detached or missing_last_event_id'`

Expected: FAIL because current reconciliation and recovery paths force terminal `error`.

**Step 3: Write minimal implementation**

In `archon/research/store.py`:

- update local reconciliation so missing monitor ownership on a nonterminal stream-backed job rewrites `stream_status` to `stream.detached` instead of `error`
- preserve `status`, `summary`, `latest_thought_summary`, and `error`
- update startup recovery so jobs without `last_event_id` are left detached and skipped instead of rewritten to terminal `error`

Implementation shape:

```python
return save_research_job(
    ResearchJobRecord(
        ...,
        status=record.status,
        summary=record.summary,
        error=record.error,
        stream_status="stream.detached",
        last_event_id=record.last_event_id,
        latest_thought_summary=record.latest_thought_summary,
    )
)
```

Do not change the real stream EOF error path or timeout path.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_research_store.py -q -k 'detached or missing_last_event_id'`

Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_research_store.py archon/research/store.py
git commit -m "fix: make detached research streams truthful"
```

### Task 3: Render detached jobs truthfully in `/job`

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `archon/research/formatting.py`

**Step 1: Write the failing test**

Add a `/job research:<id>` formatting test in `tests/test_cli.py` that passes a nonterminal record with:

- `stream_status="stream.detached"`
- `latest_thought_summary="Checking sources"`
- `status="in_progress"`

Assert that the rendered output contains:

- `job_stream_status: stream.detached`
- `job_live_status: stream detached | no live consumer`
- `job_latest_thought_summary: Checking sources`

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli.py -q -k 'detached and research'`

Expected: FAIL because `_format_research_live_status(...)` currently reports `stream active`.

**Step 3: Write minimal implementation**

In `archon/research/formatting.py`, special-case `stream_status == "stream.detached"` before the generic active-stream branch.

Implementation shape:

```python
if normalized in {"in_progress", "running", "queued", "starting"}:
    if stream_status == "stream.detached":
        return "stream detached | no live consumer"
```

Leave the existing timeout and recent-progress branches unchanged for truly active streams.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cli.py -q -k 'detached and research'`

Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_cli.py archon/research/formatting.py
git commit -m "fix: render detached research jobs truthfully"
```

### Task 4: Verify the full slice

**Files:**
- Verify: `tests/test_research.py`
- Verify: `tests/test_research_store.py`
- Verify: `tests/test_cli.py`
- Verify: `archon/research/store.py`
- Verify: `archon/research/formatting.py`

**Step 1: Run focused tests**

Run: `python -m pytest tests/test_research.py tests/test_research_store.py tests/test_cli.py -q -k 'research or detached or thought_summary'`

Expected: PASS.

**Step 2: Run the full research/CLI slice**

Run: `python -m pytest tests/test_research.py tests/test_research_store.py tests/test_cli.py -q`

Expected: PASS.

**Step 3: Run the full suite**

Run: `XDG_STATE_HOME=/tmp/archon-state python -m pytest tests -q`

Expected: PASS.

**Step 4: Sync context**

Update `AGENT_CONTEXT.json` with:

- live Deep Research thought summaries now emit through `ux.job_progress`
- detached stream-backed jobs remain nonterminal and truthful across processes
- `/job research:<id>` renders `stream.detached` as `stream detached | no live consumer`

**Step 5: Commit**

```bash
git add AGENT_CONTEXT.json archon/research/store.py archon/research/formatting.py tests/test_research.py tests/test_research_store.py tests/test_cli.py
git commit -m "fix: surface live deep research thoughts truthfully"
```
