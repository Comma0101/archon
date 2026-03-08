# Deep Research Streaming Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace Archon's polling-first Deep Research integration with a streaming-first runtime that persists local progress and completion state.

**Architecture:** Google Deep Research starts with `stream=True`, a background consumer persists local research job state from streamed events, and `/job` plus `/jobs` read that local state instead of provider polling. Cancellation remains local-first and truthful.

**Tech Stack:** Python, google-genai Interactions API, pytest, Archon job store, terminal/Telegram UX events

---

### Task 1: Add failing streaming client tests

**Files:**
- Modify: `tests/test_research.py`
- Modify: `tests/test_research_store.py`

**Step 1: Write failing tests for streamed Deep Research startup and progress**

Add tests that assert:
- `GoogleDeepResearchClient` can start a streamed interaction and expose initial interaction metadata
- stream events can be normalized into local status/progress updates
- store-level stream consumption marks completion and error correctly

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_research.py tests/test_research_store.py -q -k stream`
Expected: FAIL with missing streaming methods/helpers

**Step 3: Commit red state scaffold if helpful**

```bash
git add tests/test_research.py tests/test_research_store.py
git commit -m "test: add deep research streaming fixtures"
```

### Task 2: Implement streaming client support

**Files:**
- Modify: `archon/research/google_deep_research.py`
- Test: `tests/test_research.py`

**Step 1: Add minimal streaming API**

Implement a client path that:
- starts with `stream=True`
- yields normalized event information
- captures interaction id from `interaction.start`

**Step 2: Run focused tests**

Run: `python -m pytest tests/test_research.py -q -k stream`
Expected: PASS

**Step 3: Commit**

```bash
git add archon/research/google_deep_research.py tests/test_research.py
git commit -m "feat: add deep research streaming client"
```

### Task 3: Add streaming job persistence

**Files:**
- Modify: `archon/research/models.py`
- Modify: `archon/research/store.py`
- Test: `tests/test_research_store.py`

**Step 1: Extend research job model with stream-state fields**

Include fields such as:
- `last_event_at`
- `event_count`
- `latest_thought_summary`
- `stream_status`

**Step 2: Implement background stream consumer**

Persist local updates from stream events and emit UX job progress/completion events.

**Step 3: Run focused tests**

Run: `python -m pytest tests/test_research_store.py -q`
Expected: PASS

**Step 4: Commit**

```bash
git add archon/research/models.py archon/research/store.py tests/test_research_store.py
git commit -m "feat: persist deep research stream state"
```

### Task 4: Switch the tool path to streaming-first

**Files:**
- Modify: `archon/tooling/content_tools.py`
- Modify: `archon/research/store.py`
- Test: `tests/test_tools_content.py`

**Step 1: Make `deep_research` start streamed jobs**

Replace polling monitor startup with stream consumer startup.

**Step 2: Remove provider polling from the normal tool path**

`check_research_job` and `list_research_jobs` should use local state only.

**Step 3: Run focused tests**

Run: `python -m pytest tests/test_tools_content.py -q`
Expected: PASS

**Step 4: Commit**

```bash
git add archon/tooling/content_tools.py archon/research/store.py tests/test_tools_content.py
git commit -m "refactor: route deep research through streaming runtime"
```

### Task 5: Update shell formatting and status views

**Files:**
- Modify: `archon/research/formatting.py`
- Modify: `archon/cli_repl_commands.py`
- Test: `tests/test_cli.py`

**Step 1: Update `/job` and `/jobs` formatting for stream-driven state**

Show:
- last event time
- event count
- stream status
- latest summary/output preview

**Step 2: Remove stale polling language from the shell view**

**Step 3: Run focused tests**

Run: `python -m pytest tests/test_cli.py -q -k research`
Expected: PASS

**Step 4: Commit**

```bash
git add archon/research/formatting.py archon/cli_repl_commands.py tests/test_cli.py
git commit -m "feat: show deep research stream state in shell"
```

### Task 6: End-to-end verification and live smoke test

**Files:**
- Modify: `AGENT_CONTEXT.json`

**Step 1: Run the full suite**

Run: `XDG_STATE_HOME=/tmp/archon-state python -m pytest tests -q`
Expected: PASS

**Step 2: Run one live Deep Research smoke test**

Start a small streamed research job and verify:
- progress events arrive
- local record updates
- `/job` reflects live state

**Step 3: Sync context**

Record the streaming cutover and live validation in `AGENT_CONTEXT.json`.

**Step 4: Commit**

```bash
git add AGENT_CONTEXT.json
git commit -m "docs: sync deep research streaming cutover context"
```
