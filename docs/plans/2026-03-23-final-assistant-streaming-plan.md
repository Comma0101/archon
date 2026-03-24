# Final Assistant Streaming Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make Archon stream final assistant prose live in interactive terminal chat and Telegram while keeping tool feedback, history writes, and token accounting stable.

**Architecture:** Fix the current buffered stream path first, because `execute_turn_stream()` still receives a fully collected list of chunks today. Introduce one lightweight core stream pump that can forward deltas immediately while preserving retry and timeout behavior, then wire CLI interactive chat and Telegram to render those deltas differently.

**Tech Stack:** Python, existing `LLMClient.chat_stream()`, `Agent.run_stream()`, Click CLI, Telegram Bot API client, pytest

---

### Task 1: Lock the core streaming contract with failing tests

**Files:**
- Modify: `tests/test_agent.py`

**Step 1: Add a regression for truly incremental stream delivery**

Add a test that proves the first text delta can escape before the final response is available.

```python
def test_run_stream_emits_first_delta_before_final_response_ready(monkeypatch):
    first_chunk_seen = threading.Event()
    allow_finish = threading.Event()

    final_resp = LLMResponse(
        text="Hello world",
        tool_calls=[],
        raw_content=[{"type": "text", "text": "Hello world"}],
        input_tokens=10,
        output_tokens=5,
    )

    def _stream(*_args, **_kwargs):
        yield "Hello"
        first_chunk_seen.set()
        assert allow_finish.wait(timeout=1.0)
        yield " world"
        yield final_resp

    agent = make_agent([], stream_chunks=None)
    agent.llm.chat_stream = MagicMock(side_effect=_stream)

    chunks: list[str] = []

    def _consume():
        for chunk in agent.run_stream("hi"):
            chunks.append(chunk)
            if chunk == "Hello":
                allow_finish.set()

    worker = threading.Thread(target=_consume)
    worker.start()
    assert first_chunk_seen.wait(timeout=1.0)
    assert chunks == ["Hello"]
    worker.join(timeout=1.0)
```

**Step 2: Add a regression for pre-delta fallback**

Add a test that fails if `run_stream()` raises immediately when provider streaming breaks before the first delta and no tool call is involved.

```python
def test_run_stream_falls_back_to_buffered_final_response_before_first_delta(monkeypatch):
    final_resp = LLMResponse(
        text="buffered fallback",
        tool_calls=[],
        raw_content=[{"type": "text", "text": "buffered fallback"}],
        input_tokens=7,
        output_tokens=3,
    )
    agent = make_agent([final_resp], stream_chunks=None)
    agent.llm.chat_stream = MagicMock(side_effect=RuntimeError("stream broke"))

    chunks = list(agent.run_stream("hi"))

    assert chunks == ["buffered fallback"]
    assert agent.llm.chat.call_count == 1
    assert [msg["role"] for msg in agent.history] == ["user", "assistant"]
```

**Step 3: Add a regression that history still records one assistant turn**

```python
def test_run_stream_writes_assistant_history_once_after_stream_completion():
    final_resp = LLMResponse(
        text="done",
        tool_calls=[],
        raw_content=[{"type": "text", "text": "done"}],
        input_tokens=10,
        output_tokens=5,
    )
    stream_chunks = [["d", "o", "n", "e", final_resp]]
    agent = make_agent([], stream_chunks=stream_chunks)

    assert list(agent.run_stream("hi")) == ["d", "o", "n", "e"]
    assert [msg["role"] for msg in agent.history] == ["user", "assistant"]
```

**Step 4: Run the focused failing slice**

Run:
```bash
python -m pytest tests/test_agent.py -q -k 'run_stream and (delta or fallback or history)'
```

Expected: FAIL because the current stream path still buffers provider chunks internally and has no pre-delta fallback.

**Step 5: Commit the red tests**

```bash
git add tests/test_agent.py
git commit -m "test: require live final-text streaming semantics"
```

### Task 2: Make the core stream path truly incremental

**Files:**
- Create: `archon/streaming.py`
- Modify: `archon/agent.py`
- Modify: `archon/execution/turn_executor.py`
- Modify: `tests/test_agent.py`

**Step 1: Add a lightweight stream pump**

Create a helper that can:

- iterate `llm.chat_stream(...)` in a worker thread
- forward text deltas to a callback as soon as they arrive
- collect the final `LLMResponse`
- preserve timeout handling
- retry only before any user-visible delta has been emitted
- fall back to one non-streaming `chat()` call before any delta if streaming breaks early

Use a small queued-event shape like:

```python
@dataclass
class StreamPumpResult:
    response: LLMResponse | None
    emitted_any_text: bool
```

and a helper like:

```python
def stream_chat_with_retry(
    llm,
    system_prompt,
    history,
    tools,
    *,
    on_text_delta,
    fallback_chat,
    max_attempts=3,
    request_timeout_sec=None,
) -> StreamPumpResult:
    ...
```

**Step 2: Change the stream executor contract**

Update `execute_turn_stream()` so the streaming LLM step pushes text directly instead of returning a pre-collected list.

Change the callback shape from:

```python
llm_stream_step=lambda prompt: tuple[list[str], LLMResponse | None]
```

to:

```python
llm_stream_step=lambda prompt, on_text_delta: LLMResponse | None
```

Inside `execute_turn_stream()`:

- call `on_text_delta(chunk)` as chunks arrive
- append assistant history only once after the final response exists
- keep usage accounting tied to the final response
- preserve the current no-tool/tool-turn semantics

**Step 3: Wire `Agent.run_stream()` through the new helper**

In `archon/agent.py`:

- replace `_chat_stream_collect_with_retry()` usage with the new callback-based stream helper
- keep `run_stream()` yielding only text chunks to its caller
- keep `run()` unchanged
- preserve activity-summary clearing behavior after the turn completes

**Step 4: Run focused core verification**

Run:
```bash
python -m pytest tests/test_agent.py -q -k 'run_stream or stream_executor or usage_ledger'
```

Expected: PASS

**Step 5: Commit**

```bash
git add archon/streaming.py archon/agent.py archon/execution/turn_executor.py tests/test_agent.py
git commit -m "feat: make final-response streaming incremental"
```

### Task 3: Stream final assistant text in interactive terminal chat

**Files:**
- Modify: `archon/cli_interactive_commands.py`
- Modify: `archon/cli_ui.py`
- Modify: `archon/cli.py`
- Modify: `tests/test_cli.py`

**Step 1: Add CLI regressions for streamed terminal output**

Add tests that require:

- interactive chat to use `agent.run_stream()` for normal free-text turns
- streamed chunks to be written incrementally before the final stats line
- buffered formatting to remain in place when no chunks are emitted
- non-interactive `run_cmd()` to stay unchanged

Example shape:

```python
def test_chat_cmd_streams_final_text_chunks_before_turn_stats():
    streamed: list[str] = []
    outputs: list[tuple[str, bool]] = []

    class _Agent:
        total_input_tokens = 10
        total_output_tokens = 5
        config = SimpleNamespace(llm=SimpleNamespace(model="test-model"), telegram=SimpleNamespace(enabled=False))
        hooks = SimpleNamespace(register=lambda *_a, **_k: None)
        tools = SimpleNamespace(confirmer=None)
        session_id = "sess"

        def run_stream(self, _msg):
            yield "Hello"
            yield " world"

    _chat_cmd(
        ...,
        make_agent_fn=lambda: _Agent(),
        click_echo_fn=lambda text="", err=False: outputs.append((text, err)),
        stream_write_fn=lambda text: streamed.append(text),
        stream_flush_fn=lambda: None,
    )

    assert "".join(streamed).endswith("Hello world\n")
```

**Step 2: Add lightweight stream rendering helpers**

In `archon/cli_ui.py`, add tiny helpers for:

- opening a streamed assistant reply prefix
- writing raw assistant text chunks
- closing the streamed reply cleanly

Keep them plain text and ANSI-safe. Do not add markdown rendering or panels.

**Step 3: Wire interactive chat to `run_stream()`**

In `archon/cli_interactive_commands.py`:

- use `agent.run_stream(user_input)` for normal interactive turns
- stop the spinner on first visible delta, not after the whole call returns
- if no chunk arrives, fall back to the current `format_chat_response_fn(response)` path
- save exchange once using the final assembled text
- leave slash commands, approval replay, and local commands buffered

Add optional raw stream writer injection to `chat_cmd()` and wire it from `archon/cli.py` using `sys.stdout.write` / `sys.stdout.flush`.

**Step 4: Run focused CLI verification**

Run:
```bash
python -m pytest tests/test_cli.py -q -k 'stream or chat_cmd'
```

Expected: PASS

**Step 5: Commit**

```bash
git add archon/cli_interactive_commands.py archon/cli_ui.py archon/cli.py tests/test_cli.py
git commit -m "feat: stream final assistant text in terminal chat"
```

### Task 4: Stream final assistant text in Telegram with one throttled live reply

**Files:**
- Modify: `archon/adapters/telegram.py`
- Modify: `archon/ux/telegram_renderer.py`
- Modify: `tests/test_telegram_adapter.py`

**Step 1: Add failing Telegram regressions**

Add tests that require:

- one in-progress message is created for a streamed final answer
- later chunks update that same message through `edit_message_text`
- final completion records the settled text once
- edit failure falls back to plain send
- long final text rolls over to normal send behavior without losing content

Example shape:

```python
def test_chat_body_streams_final_reply_by_editing_one_message(monkeypatch):
    adapter = _adapter()
    sent = []
    edits = []

    class _Agent(_TelegramLocalCommandAgent):
        def run_stream(self, _text):
            yield "Hello"
            yield " world"

    monkeypatch.setattr(adapter, "_get_or_create_chat_agent", lambda _chat_id: _Agent())
    monkeypatch.setattr(adapter._bot, "send_message", lambda *a, **k: {"message_id": 321})
    monkeypatch.setattr(adapter._bot, "edit_message_text", lambda *a, **k: edits.append((a, k)))
    monkeypatch.setattr(adapter, "_send_text", lambda chat_id, text: sent.append((chat_id, text)))

    adapter._handle_message({"text": "hi", "chat": {"id": 99}, "from": {"id": 42}})

    assert edits
```

**Step 2: Add a lightweight Telegram live-reply helper**

In `archon/ux/telegram_renderer.py`, add a small helper class for streamed assistant replies, for example:

```python
class StreamingReply:
    def __init__(..., throttle_s=0.75, min_start_chars=24): ...
    def add_text(self, chunk: str) -> None: ...
    def finalize(self, final_text: str) -> None: ...
    def failover(self, final_text: str) -> None: ...
```

It should:

- buffer text
- create the first message only after meaningful text exists
- throttle edits
- stop editing after the first edit failure

**Step 3: Wire `_handle_chat_body()` to the stream path**

In `archon/adapters/telegram.py`:

- use `agent.run_stream(body)` for normal chat turns
- keep approvals, blocked-action suppression, request context, and typing behavior intact
- assemble the final reply text while streaming
- call `save_exchange()` exactly once with the final settled assistant text
- if no stream chunk arrives, fall back to the current plain send path

Keep tool events and streamed final assistant text separate.

**Step 4: Run focused Telegram verification**

Run:
```bash
python -m pytest tests/test_telegram_adapter.py -q -k 'stream or edit_message or fallback or long'
```

Expected: PASS

**Step 5: Commit**

```bash
git add archon/adapters/telegram.py archon/ux/telegram_renderer.py tests/test_telegram_adapter.py
git commit -m "feat: stream final assistant replies in telegram"
```

### Task 5: Full verification and manual smoke guidance

**Files:**
- Modify if needed: `tests/test_agent.py`
- Modify if needed: `tests/test_cli.py`
- Modify if needed: `tests/test_telegram_adapter.py`

**Step 1: Run the streaming-focused regression slice**

Run:
```bash
python -m pytest tests/test_agent.py tests/test_cli.py tests/test_telegram_adapter.py -q -k 'stream or streaming or fallback'
```

**Step 2: Run the broader cross-surface slice**

Run:
```bash
python -m pytest tests/test_agent.py tests/test_cli.py tests/test_telegram_adapter.py -q
```

**Step 3: Run the full automated suite**

Run:
```bash
python -m pytest tests -q
```

**Step 4: Manual smoke**

- start `archon chat`
- verify a normal answer appears chunk-by-chunk in terminal chat
- verify tool output still renders first and final assistant prose streams after tool completion
- verify prompt behavior is still correct after the streamed answer finishes
- start Telegram polling
- send a normal chat message and confirm one reply message updates in place
- verify a blocked dangerous action still uses the normal approval flow
- verify long replies still complete cleanly

**Step 5: Commit any final assertion updates**

```bash
git add tests/test_agent.py tests/test_cli.py tests/test_telegram_adapter.py
git commit -m "test: finalize final assistant streaming coverage"
```
