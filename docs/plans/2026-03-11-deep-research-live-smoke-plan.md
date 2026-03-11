# Deep Research Live Smoke Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add one minimal live Deep Research smoke script that exercises Archon's real stream-backed runtime with a real API key and reports stream evidence quickly.

**Architecture:** Create a single script at `scripts/deep_research_smoke.py` with a small callable `main()` so tests can drive it without live network access. The script will start a real Deep Research job through `start_research_stream_job(...)`, subscribe to `ux.job_progress`, poll the persisted job record for a bounded window, and exit based on whether real stream evidence appeared.

**Tech Stack:** Python, argparse, Archon research store, HookBus, pytest, monkeypatch, capsys

---

Execution notes:

- Use `@superpowers:test-driven-development`.
- Keep the scope to one script plus one focused test file.
- Do not add replay mode, fixtures, or extra commands in this pass.

### Task 1: Define the smoke script contract with failing tests

**Files:**
- Create: `tests/test_deep_research_smoke.py`
- Create: `scripts/deep_research_smoke.py`

**Step 1: Write the failing tests**

Add tests in `tests/test_deep_research_smoke.py` for three behaviors:

1. Missing API key exits nonzero with a clear message.
2. Timeout with observed stream evidence exits `0` and prints the final snapshot.
3. Timeout with no stream evidence exits nonzero.

Use `runpy.run_path("scripts/deep_research_smoke.py", run_name="__main__")` or an imported `main()` entrypoint. Monkeypatch:

- environment variables
- `start_research_stream_job`
- `load_research_job`
- `GoogleDeepResearchClient.from_api_key`
- `time.sleep`

Example test shape:

```python
def test_smoke_succeeds_when_progress_is_observed(monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr("archon.research.store.start_research_stream_job", fake_start)
    monkeypatch.setattr("archon.research.store.load_research_job", fake_load)
    monkeypatch.setattr("archon.research.google_deep_research.GoogleDeepResearchClient.from_api_key", fake_client_factory)
    exit_code = smoke.main(["--prompt", "test prompt", "--timeout", "1"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "latest_thought_summary: Checking sources" in out
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_deep_research_smoke.py -q`

Expected: FAIL because the script does not exist yet.

**Step 3: Write minimal implementation**

Create `scripts/deep_research_smoke.py` with:

- `argparse` handling for `--prompt` and `--timeout`
- API key lookup from `GEMINI_API_KEY` or `GOOGLE_API_KEY`
- a small `main(argv=None) -> int`
- a `HookBus` listener that prints `ux.job_progress` notices
- job start via `start_research_stream_job(...)`
- bounded polling via `load_research_job(...)`
- final snapshot printing
- exit `0` only when startup succeeded and stream evidence appeared before timeout

Keep the snapshot fields limited to:

```python
snapshot = {
    "job_id": f"research:{record.interaction_id}",
    "status": record.status,
    "provider_status": record.provider_status,
    "stream_status": record.stream_status,
    "last_event_id": record.last_event_id,
    "latest_thought_summary": record.latest_thought_summary,
    "event_count": record.event_count,
    "poll_count": record.poll_count,
    "error": record.error,
}
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_deep_research_smoke.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_deep_research_smoke.py scripts/deep_research_smoke.py
git commit -m "feat: add deep research live smoke script"
```

### Task 2: Verify the smoke tool against existing Deep Research tests

**Files:**
- Verify: `tests/test_deep_research_smoke.py`
- Verify: `tests/test_research.py`
- Verify: `tests/test_research_store.py`
- Verify: `scripts/deep_research_smoke.py`

**Step 1: Run the smoke-script tests**

Run: `python -m pytest tests/test_deep_research_smoke.py -q`

Expected: PASS.

**Step 2: Run the core Deep Research slice**

Run: `python -m pytest tests/test_research.py tests/test_research_store.py -q`

Expected: PASS.

**Step 3: Commit if any fixups were required**

```bash
git add tests/test_deep_research_smoke.py tests/test_research.py tests/test_research_store.py scripts/deep_research_smoke.py
git commit -m "test: lock deep research live smoke behavior"
```

### Task 3: Manual live smoke with a real API key

**Files:**
- Verify: `scripts/deep_research_smoke.py`

**Step 1: Run one live smoke**

Run:

```bash
python scripts/deep_research_smoke.py --prompt "how to build an ai agent in 2026 based on the trend of openclaw" --timeout 90
```

Expected success cases:

- the script prints a started job id
- one or more progress lines appear, or the final snapshot shows real stream evidence
- the final snapshot includes `event_count`, `last_event_id`, and `latest_thought_summary`
- exit code is `0` if evidence was observed before timeout

Expected failure cases:

- missing API key
- startup exception
- timeout with no stream evidence at all

**Step 2: Record context**

Update `AGENT_CONTEXT.json` with the new smoke command and the latest observed live behavior if the manual smoke taught us anything new.

**Step 3: Commit**

```bash
git add AGENT_CONTEXT.json scripts/deep_research_smoke.py tests/test_deep_research_smoke.py
git commit -m "docs: record deep research live smoke workflow"
```
