# Deep Research Truthfulness Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make native Deep Research requests truthful by refusing silent fallback when native Deep Research is disabled or fails to start.

**Architecture:** Keep the existing router and research-job path, but tighten `Agent._maybe_start_deep_research_job()` so explicit deep-research requests return a clear user-facing unavailability/failure message instead of falling through to the ordinary tool loop. Preserve the existing success path and route hook semantics.

**Tech Stack:** Python, pytest, Archon agent/router/research modules

---

### Task 1: Cover disabled deep-research truthfulness

**Files:**
- Modify: `tests/test_agent.py`

**Step 1: Write the failing test**

Add a test asserting that when `google_deep_research.enabled = False` and the request is classified as deep research, `agent.run(...)` returns an explicit unavailability message and does not call `llm.chat()`.

**Step 2: Run test to verify it fails**

Run: `XDG_STATE_HOME=/tmp/archon-state python -m pytest tests/test_agent.py -q -k "disabled_deep_research"`
Expected: FAIL because current behavior falls through to the normal loop.

**Step 3: Write minimal implementation**

Adjust `archon/agent.py` so `_maybe_start_deep_research_job()` returns a clear disabled message for explicit deep-research requests instead of `None`.

**Step 4: Run test to verify it passes**

Run: `XDG_STATE_HOME=/tmp/archon-state python -m pytest tests/test_agent.py -q -k "disabled_deep_research"`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_agent.py archon/agent.py
git commit -m "fix: make disabled deep research explicit"
```

### Task 2: Cover startup-failure truthfulness

**Files:**
- Modify: `tests/test_agent.py`
- Modify: `archon/agent.py`

**Step 1: Write the failing test**

Add a test asserting that when native Deep Research is enabled but `_create_google_deep_research_client()` or `start_research()` raises, `agent.run(...)` returns a clear startup-failure message and does not call `llm.chat()`.

**Step 2: Run test to verify it fails**

Run: `XDG_STATE_HOME=/tmp/archon-state python -m pytest tests/test_agent.py -q -k "startup_failure_deep_research"`
Expected: FAIL because current behavior silently falls through.

**Step 3: Write minimal implementation**

Handle startup exceptions in `_maybe_start_deep_research_job()` by returning a direct failure message, while still emitting `research.failed` for hooks.

**Step 4: Run test to verify it passes**

Run: `XDG_STATE_HOME=/tmp/archon-state python -m pytest tests/test_agent.py -q -k "startup_failure_deep_research"`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_agent.py archon/agent.py
git commit -m "fix: surface deep research startup failures"
```

### Task 3: Preserve successful route semantics

**Files:**
- Modify: `tests/test_agent.py`
- Modify: `archon/agent.py` (only if needed)

**Step 1: Verify existing success tests still cover route emission**

Ensure the existing success-path deep-research tests still assert:
- `Research job started: research:<id>`
- `agent.llm.chat.call_count == 0`
- route hook payload uses `hybrid_deep_research_job_v0`

**Step 2: Run focused deep-research tests**

Run: `XDG_STATE_HOME=/tmp/archon-state python -m pytest tests/test_agent.py -q -k "deep_research"`
Expected: PASS

**Step 3: Commit**

```bash
git add tests/test_agent.py archon/agent.py
git commit -m "test: preserve deep research job semantics"
```

### Task 4: Verify full suite and sync context

**Files:**
- Modify: `AGENT_CONTEXT.json`

**Step 1: Sync context**

Add one changelog entry describing the truthfulness fix and the final verification counts.

**Step 2: Run full suite**

Run: `XDG_STATE_HOME=/tmp/archon-state python -m pytest tests -q`
Expected: PASS

**Step 3: Commit**

```bash
git add AGENT_CONTEXT.json
git commit -m "docs: sync deep research truthfulness context"
```
