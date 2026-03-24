# Streaming Correctness Cleanup Design

## Goal

Fix the four concrete correctness bugs left in the new final-assistant-streaming path without expanding into broader refactors.

## Scope

This phase is intentionally narrow. It covers only:

1. Telegram no-chunk turns rerunning the same request.
2. CLI interactive no-chunk turns rendering and saving an empty assistant reply.
3. Pre-delta buffered fallback performing more than one `llm.chat()` call.
4. Telegram long streamed replies duplicating already-sent content after crossing the edit limit.

Non-goals:

- no broader agent-loop refactor
- no Telegram transport rewrite
- no markdown or rendering polish
- no token-usage optimization work
- no general code-health cleanup outside these four behaviors

## Architecture

The fix should preserve the current streaming architecture:

- `archon/streaming.py` remains the shared final-text streaming primitive
- `archon/execution/turn_executor.py` remains the shared streaming executor
- CLI and Telegram continue to render the same streamed final assistant text differently

The cleanup should tighten return contracts rather than layering more fallback branches on top.

## Behavior Changes

### 1. Telegram no-chunk turns must finish once, not twice

Current bug:

- `_handle_chat_body()` calls `_stream_final_reply(...)`
- if that returns `None`, it immediately falls back to `agent.run(body)`
- a `run_stream()` implementation that finishes without visible text can execute the turn once in `run_stream()` and then again in `run()`

Desired behavior:

- `_stream_final_reply()` must return structured completion information, not just `str | None`
- Telegram should know whether the turn already ran, whether any text was emitted, and what final text to use
- if `run_stream()` completed the turn but emitted no chunks, Telegram should send the final text once and record the turn once
- `agent.run(body)` should be used only when streaming is unavailable, not when the streaming turn already completed

### 2. CLI interactive no-chunk turns must fall back to final text

Current bug:

- interactive chat iterates `agent.run_stream(...)`
- if no chunks arrive, `response` stays empty
- the REPL saves and renders `""`

Desired behavior:

- the interactive path should still use `run_stream()`
- when that turn completes without visible chunks, the CLI should render the final assistant text once using the normal buffered formatter
- saved history output must contain the actual final assistant reply, not `""`

### 3. Pre-delta buffered fallback must be exactly one buffered call

Current bug:

- `stream_chat_with_retry()` delegates pre-delta fallback through `_chat_with_retry(...)`
- this can retry buffered fallback multiple times

Desired behavior:

- after streaming retries are exhausted before any visible delta, fallback should perform exactly one buffered `llm.chat(...)` call
- if that one buffered call fails, the error should propagate
- this keeps cost and side-effect behavior aligned with the approved plan

### 4. Telegram long streamed replies must not duplicate the sent prefix

Current bug:

- `LiveReplyEditor` can send an initial short live message
- if the final text later exceeds Telegram edit limits, it falls back to sending the whole final text
- users can see the prefix once in the live message and again in the fallback message

Desired behavior:

- keep the already-sent live prefix intact
- when the final text crosses the edit limit, send only the remainder after the last successfully sent text
- if no live text was ever sent, the fallback can send the whole text normally

## Error Handling

- If Telegram edit fails, fallback remains one-way: stop editing and switch to plain sends.
- If a streamed turn is blocked by approval, both CLI and Telegram must suppress streamed safety-gate text and show only the approval prompt.
- If buffered fallback fails after streaming produced no visible text, surface the error normally; do not try extra hidden retries.

## Testing Strategy

Add one regression per bug:

1. Telegram no-chunk `run_stream()` must not call `run()` afterward.
2. CLI no-chunk `run_stream()` must still display and save the final assistant text.
3. Pre-delta fallback must call `llm.chat()` exactly once.
4. Telegram long streamed replies must only send the unsent remainder after the live prefix.

Keep the broader streaming regression slices and the full suite green after the targeted fixes.

## Acceptance Criteria

- Telegram never reruns a no-chunk streamed turn.
- CLI interactive chat never saves or renders an empty assistant reply for a completed no-chunk streamed turn.
- pre-delta buffered fallback uses exactly one buffered LLM call.
- Telegram long streamed replies never duplicate already-sent text.
- existing streaming, approval, and tool-feedback regressions remain green.
