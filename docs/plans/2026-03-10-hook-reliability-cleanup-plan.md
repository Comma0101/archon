# Hook Reliability Cleanup Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove the worker global hook-bus seam and make hook handler failures observable while preserving best-effort semantics.

**Architecture:** Use explicit `HookBus` plumbing for worker completion events and keep `HookBus.emit()` non-fatal, but record per-handler failures in lightweight in-memory diagnostics.

**Tech Stack:** Python, pytest, existing Archon control/worker modules.

---

### Task 1: Add failing tests for hook failure diagnostics

**Files:**
- Create: `tests/test_hooks.py`
- Modify: `archon/control/hooks.py`

**Step 1: Write the failing tests**
- Add a test that registers two handlers on `HookBus`, where the first raises and the second records delivery.
- Assert the second handler still runs.
- Assert failure diagnostics are recorded.

**Step 2: Run test to verify it fails**
Run: `python -m pytest tests/test_hooks.py -q`
Expected: FAIL because diagnostics accessor does not exist yet.

**Step 3: Write minimal implementation**
- Add internal hook-failure storage and an accessor to `archon/control/hooks.py`.

**Step 4: Run test to verify it passes**
Run: `python -m pytest tests/test_hooks.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add archon/control/hooks.py tests/test_hooks.py
git commit -m "fix: record hook handler failures"
```

### Task 2: Add failing tests for worker explicit hook-bus emission

**Files:**
- Modify: `tests/test_worker_session_store.py`
- Modify: `archon/workers/session_store.py`

**Step 1: Write the failing tests**
- Add a test that calls the worker completion emission path with an explicit `HookBus` and observes `ux.job_completed`.
- Add a test that verifies no global `_hook_bus` attribute is needed.

**Step 2: Run test to verify it fails**
Run: `python -m pytest tests/test_worker_session_store.py -q -k hook`
Expected: FAIL because the completion helper still reads global function state.

**Step 3: Write minimal implementation**
- Thread `hook_bus` into the completion helper.
- Remove `_emit_job_completed_event._hook_bus` lookup.

**Step 4: Run test to verify it passes**
Run: `python -m pytest tests/test_worker_session_store.py -q -k hook`
Expected: PASS

**Step 5: Commit**
```bash
git add archon/workers/session_store.py tests/test_worker_session_store.py
git commit -m "refactor: remove worker global hook bus"
```

### Task 3: Verify the full affected slice and sync context

**Files:**
- Modify: `AGENT_CONTEXT.json`

**Step 1: Run focused verification**
Run: `python -m pytest tests/test_hooks.py tests/test_worker_session_store.py tests/test_research.py tests/test_cli.py -q`
Expected: PASS

**Step 2: Run full suite**
Run: `XDG_STATE_HOME=/tmp/archon-state python -m pytest tests -q`
Expected: PASS

**Step 3: Sync context**
- Update `AGENT_CONTEXT.json` with hook cleanup status and test totals.

**Step 4: Commit**
```bash
git add AGENT_CONTEXT.json
git commit -m "docs: sync hook reliability cleanup context"
```
