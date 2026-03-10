# Deep Research Reliability Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Resume non-terminal streaming Deep Research jobs after an Archon restart so persisted jobs continue to completion instead of becoming stale local records.

**Architecture:** Add a once-per-process recovery bootstrap in `Agent.__init__` that calls a new store-level recovery function. The store reuses the existing config-backed Google client and stream-consumption loop to resume jobs from `last_event_id`.

**Tech Stack:** Python, pytest, existing Archon research store/client modules.

---

### Task 1: Add failing research-store recovery tests

**Files:**
- Modify: `tests/test_research_store.py`
- Modify: `archon/research/store.py`

**Step 1: Write the failing tests**
- Add a test for a persisted non-terminal stream job that should resume and complete from `last_event_id`.
- Add a test for a persisted non-terminal stream job with no `last_event_id` that should become terminal error.

**Step 2: Run tests to verify they fail**
Run: `python -m pytest tests/test_research_store.py -q -k 'resume_on_startup or missing_last_event_id'`
Expected: FAIL because startup recovery does not exist yet.

**Step 3: Write minimal implementation**
- Add a recovery entry point in `archon/research/store.py`.
- Add a background resume worker path for persisted jobs.

**Step 4: Run tests to verify they pass**
Run: `python -m pytest tests/test_research_store.py -q -k 'resume_on_startup or missing_last_event_id'`
Expected: PASS

**Step 5: Commit**
```bash
git add archon/research/store.py tests/test_research_store.py
git commit -m "fix: resume deep research jobs after restart"
```

### Task 2: Add failing once-only agent bootstrap test

**Files:**
- Modify: `tests/test_agent.py`
- Modify: `archon/agent.py`

**Step 1: Write the failing test**
- Add a test that instantiates two `Agent`s and asserts startup recovery is only triggered once per process.

**Step 2: Run test to verify it fails**
Run: `python -m pytest tests/test_agent.py -q -k 'deep_research_recovery_once'`
Expected: FAIL because no bootstrap/guard exists yet.

**Step 3: Write minimal implementation**
- Add a once-only startup recovery call in `Agent.__init__`.

**Step 4: Run test to verify it passes**
Run: `python -m pytest tests/test_agent.py -q -k 'deep_research_recovery_once'`
Expected: PASS

**Step 5: Commit**
```bash
git add archon/agent.py tests/test_agent.py
git commit -m "feat: bootstrap deep research recovery"
```

### Task 3: Verify the affected slice and sync context

**Files:**
- Modify: `AGENT_CONTEXT.json`

**Step 1: Run focused verification**
Run: `python -m pytest tests/test_research_store.py tests/test_research.py tests/test_agent.py tests/test_cli.py -q`
Expected: PASS

**Step 2: Run full suite**
Run: `XDG_STATE_HOME=/tmp/archon-state python -m pytest tests -q`
Expected: PASS

**Step 3: Sync context**
- Update `AGENT_CONTEXT.json` with the Deep Research restart-recovery status and test totals.

**Step 4: Commit**
```bash
git add AGENT_CONTEXT.json
git commit -m "docs: sync deep research reliability context"
```
