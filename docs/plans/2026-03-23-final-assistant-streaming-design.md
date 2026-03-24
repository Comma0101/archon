# Final Assistant Streaming Design

**Date:** 2026-03-23  
**Branch:** `master`

## Context

Archon already feels more alive during tool execution:

- tool events stream through the shared UX event path
- shell output can stream live
- edit operations show diffs
- Telegram now has safer batching and better transport health handling

The remaining major UX gap is final assistant prose. The model can already stream text deltas in [archon/llm.py](/home/comma/Documents/archon/archon/llm.py), and Archon already exposes [Agent.run_stream()](/home/comma/Documents/archon/archon/agent.py), but the surfaces still feel bursty because the final answer is mostly shown only after completion.

This phase closes that gap without redesigning the whole turn loop.

## Goals

- Stream final assistant text in interactive terminal chat.
- Stream final assistant text in Telegram through one throttled in-progress message.
- Keep tool execution UX unchanged.
- Keep persistence, memory behavior, and token accounting tied to the final settled response.
- Preserve buffered behavior for non-interactive paths.

## Non-goals

- No markdown renderer.
- No `rich`, curses, or TUI work.
- No tool-loop streaming redesign.
- No token-efficiency rewrite.
- No Telegram webhook or service rewrite.
- No broader UI chrome or panel framing pass.

## Recommended Approach

Implement one shared final-text streaming pipeline and let each surface render it differently.

### Why this approach

- It reuses existing streaming support in the LLM and agent layers.
- It avoids duplicating logic in CLI and Telegram.
- It limits risk by leaving tool execution semantics untouched.
- It keeps Archon lightweight.

## Core Behavior

Only the final assistant prose should stream.

- Tool call iterations remain governed by the current tool-event system.
- Tool summaries, shell output lines, edit diffs, approvals, and blocked actions keep their current paths.
- The streaming surface consumes final text deltas plus one final settled response.
- History records the assistant message once, after the final response is complete.

This preserves the current control flow while improving perceived responsiveness.

## Shared Streaming Model

The shared model should expose:

- text deltas as they arrive
- the final settled assistant text
- completion/failure state

The shared model should not change:

- how token usage is recorded
- how compaction is triggered
- how memory prefetch or persistence works
- how tool results enter history

The final `LLMResponse` remains the source of truth for:

- the stored assistant history entry
- input/output token accounting
- provider metadata

## CLI Behavior

Interactive terminal chat should stream final assistant text by default.

### Rules

- Stream only in interactive chat.
- Keep non-interactive or script-friendly paths buffered.
- Let tool events render exactly as they do now.
- Once final assistant text begins, print deltas into one live assistant response.
- Finish with a clean line ending and the current stored final response.

### Failure handling

- If streaming fails before any text is shown, fall back to the buffered final response.
- If streaming fails after partial text is shown, emit a short continuation notice and finish cleanly with the recovered final text if available.
- Prompt redraw and input behavior must remain correct.

## Telegram Behavior

Telegram should stream final assistant text through one editable in-progress message.

### Rules

- Do not send a new Telegram message for every chunk.
- Create one in-progress reply when meaningful final text begins.
- Edit that message on a throttle while the content grows.
- Settle that message as the final assistant response at completion.

### Failure handling

- Do not edit on every token; apply a throttle window.
- Do not start streaming on tiny noise; wait for meaningful text or a short startup timeout.
- If an edit fails, fall back once to normal send behavior and stop editing that reply.
- If the final response exceeds Telegram limits, settle the in-progress message and continue with normal chunked sends.

This keeps Telegram readable and avoids hammering `editMessageText`.

## Guardrails

### Shared guardrails

- No partial history writes.
- No partial memory writes.
- No changes to approval semantics.
- No changes to blocked-action behavior.
- No transport-specific failure should corrupt the final stored answer.

### CLI guardrails

- Streaming stays limited to interactive chat.
- Existing tool-event rendering remains intact.
- Prompt/input behavior must not regress.

### Telegram guardrails

- Use throttled edits instead of token-level edits.
- Keep batching and tool-event output separate from final-answer streaming.
- If edit-based streaming degrades, fall back to plain send reliably.

## Rollout

### Slice 1: Shared streaming primitives

- Add a small helper around `Agent.run_stream()` consumption.
- Return deltas plus final settled text/completion outcome.
- Add tests for fallback and “history written once”.

### Slice 2: CLI interactive streaming

- Wire interactive chat to consume final deltas live.
- Keep non-interactive behavior buffered.
- Add PTY-style regressions to protect prompt behavior.

### Slice 3: Telegram throttled message editing

- Add one in-progress reply path.
- Add throttled edit updates.
- Add fallback behavior for edit failures and long messages.

## Acceptance Criteria

- Interactive terminal chat shows final assistant text incrementally.
- Telegram shows one live-updating in-progress assistant reply.
- History records the assistant reply only once.
- Existing tool feedback still works.
- Buffered non-interactive behavior remains unchanged.
- Full tests remain green.

## Risks

### Prompt/rendering regression in CLI

Streaming text can conflict with prompt redraw logic.

Mitigation:

- keep CLI streaming limited to interactive chat
- add PTY-style regressions
- reuse current terminal-output seams instead of inventing a second renderer

### Edit churn or rate issues in Telegram

Frequent `editMessageText` calls can fail or look noisy.

Mitigation:

- use a throttle window
- require meaningful buffered text before starting
- fall back to normal sends if edits fail

### Double-writing assistant history

If surfaces stream and the final response also appends normally, history could duplicate the assistant turn.

Mitigation:

- keep history append tied to the final settled response object only
- add explicit regression coverage

## Outcome

This phase should make Archon feel much closer to Claude Code responsiveness without making the architecture heavier. It improves the user-facing loop at the exact point where Archon still feels delayed, while keeping tool behavior, storage, and cost semantics stable.
