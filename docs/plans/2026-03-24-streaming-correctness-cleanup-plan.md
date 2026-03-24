# Streaming Correctness Cleanup Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix the four known correctness bugs in Archon’s final-assistant-streaming path without broadening into refactors.

**Architecture:** Keep the current shared streaming structure, but tighten the contracts between the stream pump, the executor, and the CLI/Telegram surfaces. Fix each bug with a targeted regression first, then apply the minimum code change that makes the regression pass.

**Tech Stack:** Python, pytest, Click CLI, Telegram Bot API client, existing `Agent.run_stream()` / `LLMClient.chat_stream()` flow

---

### Task 1: Lock the no-chunk fallback bugs with failing tests

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `tests/test_telegram_adapter.py`
- Modify: `tests/test_agent.py`

**Step 1: Add a failing CLI no-chunk regression**

Add a test that uses an interactive agent whose `run_stream()` yields no chunks but still completes the turn, and assert:

- the final assistant text is rendered through the buffered terminal path
- the saved exchange stores the final assistant text
- no empty assistant reply is recorded

**Step 2: Run the focused CLI test**

Run:
```bash
python -m pytest tests/test_cli.py -q -k 'no_chunk'
```

Expected: FAIL because the current interactive path saves/renders `""`.

**Step 3: Add a failing Telegram no-chunk regression**

Add a test that uses an agent where:

- `run_stream()` completes the turn without yielding any chunks
- `run()` raises if called

Assert Telegram sends exactly one final reply and never calls `run()`.

**Step 4: Run the focused Telegram no-chunk test**

Run:
```bash
python -m pytest tests/test_telegram_adapter.py -q -k 'no_chunk'
```

Expected: FAIL because `_handle_chat_body()` reruns the turn via `run()`.

**Step 5: Add a failing one-shot buffered fallback regression**

Add an agent test proving that after pre-delta stream failure, the buffered fallback path calls `llm.chat()` exactly once.

**Step 6: Run the focused agent test**

Run:
```bash
python -m pytest tests/test_agent.py -q -k 'fallback and exactly_once'
```

Expected: FAIL because the current fallback uses `_chat_with_retry(...)`.

**Step 7: Commit the red tests**

```bash
git add tests/test_cli.py tests/test_telegram_adapter.py tests/test_agent.py
git commit -m "test: lock streaming correctness regressions"
```

### Task 2: Fix CLI and Telegram no-chunk turn handling

**Files:**
- Modify: `archon/cli_interactive_commands.py`
- Modify: `archon/adapters/telegram.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_telegram_adapter.py`

**Step 1: Fix CLI interactive no-chunk completion**

Update `chat_cmd()` so the streaming path distinguishes:

- streaming unavailable
- streaming completed with visible chunks
- streaming completed with zero visible chunks but a final assistant text

If zero visible chunks completed the turn, render and save the final text via the normal buffered path.

**Step 2: Run the focused CLI regression**

Run:
```bash
python -m pytest tests/test_cli.py -q -k 'no_chunk'
```

Expected: PASS

**Step 3: Fix Telegram no-chunk completion**

Refactor `_stream_final_reply()` to return structured completion data, for example:

- `completed_turn`
- `final_text`
- `delivered_live`

Then update `_handle_chat_body()` so:

- `agent.run(body)` is used only when streaming is unavailable
- a completed no-chunk streamed turn sends/records its final text once

**Step 4: Run the focused Telegram regression**

Run:
```bash
python -m pytest tests/test_telegram_adapter.py -q -k 'no_chunk'
```

Expected: PASS

**Step 5: Run a focused cross-surface slice**

Run:
```bash
python -m pytest tests/test_cli.py tests/test_telegram_adapter.py -q -k 'stream or no_chunk'
```

Expected: PASS

**Step 6: Commit**

```bash
git add archon/cli_interactive_commands.py archon/adapters/telegram.py tests/test_cli.py tests/test_telegram_adapter.py
git commit -m "fix: handle no-chunk streamed replies correctly"
```

### Task 3: Make pre-delta buffered fallback exactly one-shot

**Files:**
- Modify: `archon/streaming.py`
- Modify: `archon/agent.py`
- Test: `tests/test_agent.py`

**Step 1: Replace retrying buffered fallback with a one-shot callback**

Change the streaming fallback wiring so the pre-delta buffered path performs exactly one `llm.chat(...)` call with timeout handling, but no internal retry loop.

Do not change the streaming retry behavior itself. Only the buffered fallback should become one-shot.

**Step 2: Run the focused agent regression**

Run:
```bash
python -m pytest tests/test_agent.py -q -k 'fallback and exactly_once'
```

Expected: PASS

**Step 3: Run the broader stream agent slice**

Run:
```bash
python -m pytest tests/test_agent.py -q -k 'run_stream or stream_executor or fallback'
```

Expected: PASS

**Step 4: Commit**

```bash
git add archon/streaming.py archon/agent.py tests/test_agent.py
git commit -m "fix: make buffered stream fallback one-shot"
```

### Task 4: Prevent Telegram long streamed reply duplication

**Files:**
- Modify: `archon/ux/telegram_renderer.py`
- Modify: `archon/adapters/telegram.py`
- Test: `tests/test_telegram_renderer.py`
- Test: `tests/test_telegram_adapter.py`

**Step 1: Add a failing long-reply duplication regression**

Add a helper-level test showing:

- a live reply already sent a prefix
- the final text exceeds the Telegram edit limit
- fallback sends only the unsent remainder, not the whole final text

Add one adapter-level test if needed to prove the same behavior through Telegram chat handling.

**Step 2: Run the focused long-reply test**

Run:
```bash
python -m pytest tests/test_telegram_renderer.py tests/test_telegram_adapter.py -q -k 'long or remainder or duplicate'
```

Expected: FAIL because the current fallback sends the entire final text.

**Step 3: Fix `LiveReplyEditor.finalize()`**

Track the last successfully sent text and, when switching to plain-send fallback because of message-length growth, send only the unsent suffix.

Preserve the existing behavior when no live text was ever sent.

**Step 4: Re-run the focused long-reply tests**

Run:
```bash
python -m pytest tests/test_telegram_renderer.py tests/test_telegram_adapter.py -q -k 'long or remainder or duplicate'
```

Expected: PASS

**Step 5: Commit**

```bash
git add archon/ux/telegram_renderer.py archon/adapters/telegram.py tests/test_telegram_renderer.py tests/test_telegram_adapter.py
git commit -m "fix: avoid duplicate telegram streamed replies"
```

### Task 5: Final verification and review

**Files:**
- Verify only

**Step 1: Run the streaming-focused regression slice**

Run:
```bash
python -m pytest tests/test_agent.py tests/test_cli.py tests/test_telegram_adapter.py tests/test_telegram_renderer.py -q -k 'stream or streaming or fallback or no_chunk or long'
```

Expected: PASS

**Step 2: Run the full suite**

Run:
```bash
python -m pytest tests -q
```

Expected: PASS

**Step 3: Review the final diff**

Run:
```bash
git diff --stat HEAD~4..HEAD
```

Confirm the phase stayed narrow and touched only the intended streaming correctness seams.

**Step 4: Commit any final test-only fixes if needed**

```bash
git add -A
git commit -m "test: finalize streaming correctness cleanup"
```
