# Tool Registry Modularization (Phase 2: Worker Tools) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extract worker-related tool registrations and delegate/session helper routines from `archon/tools.py` into `archon/tooling/worker_tools.py` without changing tool behavior, outputs, or public imports.

**Architecture:** Keep `ToolRegistry` and its public import path in `archon/tools.py`. Move worker-tool closure registration and worker-specific local helpers into `archon/tooling/worker_tools.py`, using the same worker backend APIs from `archon/workers/*` and shared truncation helper from `archon/tooling/common.py`.

**Tech Stack:** Python stdlib + existing `pytest` suite

---

## Scope (Phase 2 only)

- Move from `archon/tools.py` into `archon/tooling/worker_tools.py`:
  - `delegate_code_task`
  - `worker_status`
  - `worker_list`
  - `worker_start`
  - `worker_send`
  - `worker_poll`
  - `worker_cancel`
  - `worker_approve`
  - `worker_reconcile`
  - worker-specific local helper functions used by these registrations
- Keep in `archon/tools.py`:
  - `ToolRegistry` class and affinity methods
  - generic `execute/register` plumbing
  - imports/public module identity

## Files

### Create
- `archon/tooling/worker_tools.py`

### Modify
- `archon/tooling/__init__.py`
- `archon/tools.py`
- `tests/test_tools.py`

### Test
- `tests/test_tools.py`
- `tests/test_worker_*` (regression only)

---

### Task 1: Baseline worker tool tests

**Step 1: Run focused worker tool tests**

Run: `HOME=/tmp PYTHONPATH=/home/comma/.local/lib/python3.14/site-packages python -m pytest tests/test_tools.py -q -k 'DelegateCodeTask or worker_'`
Expected: PASS (baseline before mechanical move)

**Step 2: Verify no production changes yet**

Run: `git diff --stat`
Expected: only plan file in this phase before code extraction

---

### Task 2: Extract worker registrations and helper routines

**Step 1: Write minimal implementation**

- Create `archon/tooling/worker_tools.py`
- Move worker-specific helpers from `archon/tools.py`:
  - `_runtime_quiet_seconds`
  - delegate execution planner helpers
  - session reuse detection helpers
  - `_find_latest_worker_session_for_repo`
- Move worker registrations + nested closures:
  - `delegate_code_task`, `worker_status`, `worker_list`, `worker_start`, `worker_send`, `worker_poll`, `worker_cancel`, `worker_approve`, `worker_reconcile`
- Provide `register_worker_tools(registry)`
- Use imports from `archon.workers` and `archon.safety` directly

**Step 2: Wire `ToolRegistry._register_builtins()`**

- Replace inlined worker registration block with `register_worker_tools(self)`
- Keep tool registration order unchanged (23 tools total)

**Step 3: Remove dead code/imports from `archon/tools.py`**

- Drop moved helper functions and worker backend imports no longer needed there

---

### Task 3: Update tests (monkeypatch target paths only)

**Step 1: Patch monkeypatch paths**

- Change worker-related monkeypatches in `tests/test_tools.py` from `archon.tools.*` to `archon.tooling.worker_tools.*` where closures now resolve symbols
- Keep assertions/behavior checks identical

**Step 2: Run focused tool tests**

Run: `HOME=/tmp PYTHONPATH=/home/comma/.local/lib/python3.14/site-packages python -m pytest tests/test_tools.py -q`
Expected: PASS

---

### Task 4: Full regression verification

**Step 1: Run full suite**

Run: `HOME=/tmp PYTHONPATH=/home/comma/.local/lib/python3.14/site-packages python -m pytest tests/ -q`
Expected: PASS (currently 167)

**Step 2: Record state**

- Update `AGENT_CONTEXT.json` with:
  - new `archon/tooling/worker_tools.py`
  - `archon/tools.py` role shift (registry + public wrapper)
  - test count (unchanged if green)

---

## Out of Scope (Next Phases)

1. Split `tests/test_tools.py` into domain files
2. Deduplicate worker adapter helpers in `archon/workers/*`
3. Consolidate Telegram Bot API client used by `adapters/telegram.py` and `news/runner.py`
