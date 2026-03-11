# Deep Research Debug Events Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an env-gated debug mode that shows raw Deep Research provider event shape and normalized parsing results during live runs.

**Architecture:** Instrument the stream-event coercion path in `archon/research/google_deep_research.py` so the debug output observes the real provider-to-Archon translation boundary. Keep the feature off by default and bounded to a small number of events.

**Tech Stack:** Python, pytest, monkeypatch, stderr capture, existing Deep Research stream coercion logic

---

Execution notes:

- Use `@superpowers:test-driven-development`.
- Keep the scope limited to `archon/research/google_deep_research.py` and `tests/test_research.py`.
- No store changes and no new persistent state in this pass.

### Task 1: Add failing tests for env-gated debug output

**Files:**
- Modify: `tests/test_research.py`
- Modify: `archon/research/google_deep_research.py`

**Step 1: Write the failing tests**

Add focused tests in `tests/test_research.py` that:

1. set `ARCHON_DEEP_RESEARCH_DEBUG=1`
2. use a fake streamed event with nested `delta.content.text`
3. run the existing stream coercion path
4. capture `stderr`
5. assert the debug line includes:
   - raw event type
   - whether raw `event_id` exists
   - whether nested text path exists
   - normalized `delta_type`
   - whether normalized text exists

Add a second test proving that with the env flag unset, the same coercion emits no debug output.

Example test shape:

```python
monkeypatch.setenv("ARCHON_DEEP_RESEARCH_DEBUG", "1")
stream = client.start_research_stream("Research LA restaurant market")
list(stream.events)
captured = capsys.readouterr()
assert "deep-research-debug" in captured.err
assert "normalized_delta_type=thought_summary" in captured.err
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_research.py -q -k 'deep_research_debug'`

Expected: FAIL because the debug output does not exist yet.

**Step 3: Write minimal implementation**

In `archon/research/google_deep_research.py`:

- add a tiny env check helper for `ARCHON_DEEP_RESEARCH_DEBUG`
- add a small bounded counter for logged events
- emit one compact `stderr` line during stream coercion for the first few events only

Implementation shape:

```python
if _deep_research_debug_enabled() and counter < _DEBUG_EVENT_LIMIT:
    print("[deep-research-debug] ...", file=sys.stderr)
```

Do not print full text payloads. Print presence markers and normalized summaries only.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_research.py -q -k 'deep_research_debug'`

Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_research.py archon/research/google_deep_research.py
git commit -m "debug: add deep research raw event tracing"
```

### Task 2: Verify the core Deep Research slice still passes

**Files:**
- Verify: `tests/test_research.py`
- Verify: `tests/test_research_store.py`
- Verify: `tests/test_deep_research_smoke.py`
- Verify: `archon/research/google_deep_research.py`

**Step 1: Run the focused debug tests**

Run: `python -m pytest tests/test_research.py -q -k 'deep_research_debug'`

Expected: PASS.

**Step 2: Run the main Deep Research slice**

Run: `python -m pytest tests/test_research.py tests/test_research_store.py tests/test_deep_research_smoke.py -q`

Expected: PASS.

**Step 3: Commit if needed**

```bash
git add tests/test_research.py tests/test_research_store.py tests/test_deep_research_smoke.py archon/research/google_deep_research.py
git commit -m "test: verify deep research debug tracing"
```

### Task 3: Run one live smoke with debug enabled

**Files:**
- Verify: `scripts/deep_research_smoke.py`
- Verify: `archon/research/google_deep_research.py`

**Step 1: Run the live smoke**

Run:

```bash
XDG_STATE_HOME=/tmp/archon-state ARCHON_DEEP_RESEARCH_DEBUG=1 python scripts/deep_research_smoke.py --prompt "how to build an ai agent in 2026 based on the trend of openclaw" --timeout 90
```

Expected:

- debug lines appear on `stderr` for the first few streamed events
- output makes it obvious whether `event_id` and thought-summary fields are present in raw provider events
- the final snapshot still prints as before

**Step 2: Record findings**

Update `AGENT_CONTEXT.json` only if the live smoke reveals a stable new fact about Google's event shape or Archon's parser behavior.

**Step 3: Commit**

```bash
git add AGENT_CONTEXT.json archon/research/google_deep_research.py tests/test_research.py
git commit -m "docs: record deep research debug smoke findings"
```
