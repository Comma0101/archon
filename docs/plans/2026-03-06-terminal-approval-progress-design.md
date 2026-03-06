# Terminal Approval And Progress UX Design

## Decision

Evolve terminal approvals into a small, session-scoped shell surface that feels closer to Claude Code:

- text-first
- inspectable
- replayable
- minimal by default

Do not build a dashboard. Do not add a multi-step TUI. Keep the fast path unchanged.

## Problem

Archon's terminal shell has stronger command and status UX now, but dangerous-action approvals still fall back to a raw blocking `y/N` prompt from `safety.confirm()`.

That creates three problems:

1. it does not match the rest of the shell UX
2. blocked actions are not inspectable or replayable
3. it is inconsistent with Telegram and worker approval flows, which already have explicit pending-request state

There is also one adjacent control-flow issue in Telegram: new local shell commands can force chat-agent creation too early, which means commands that should stay local can fail on broken model configuration.

## Product Goals

1. Make dangerous-action approvals feel like part of the shell, not a subprocess prompt.
2. Keep local control actions local.
3. Stay lightweight and text-first.
4. Reuse the simplest parts of the Telegram approval mental model.
5. Avoid queue complexity in the first phase.

## Non-Goals

This phase will not add:

- a persistent dashboard
- a multi-item approval queue
- cross-session approval persistence
- model-generated approval reasoning
- plugin-specific approval UIs
- auto-refreshing terminal panels

## UX Model

Terminal approvals become a session-scoped approval surface.

New local shell commands:

- `/approvals`
- `/approvals on`
- `/approvals off`
- `/approve`
- `/deny`
- `/approve_next`

Behavior:

- `SAFE` actions continue immediately.
- `FORBIDDEN` actions remain hard-blocked.
- `DANGEROUS` actions create or replace one pending approval request for the current terminal session.
- the shell prints one compact approval-required message instead of raw `Execute? [y/N]`
- `/approve` replays the blocked user turn once
- `/deny` clears the pending request
- `/approve_next` allows one dangerous action only
- `/approvals on` enables sticky dangerous-action approvals for the current session
- `/approvals off` disables sticky approval mode for the current session

The pending request model stays intentionally simple:

- at most one pending request per terminal session
- newest blocked request replaces the older one
- requests expire after a short TTL

This mirrors the current Telegram approach without importing Telegram-specific callback UX.

## Architecture

### 1. Terminal Session Approval State

Add a small approval state object owned by the interactive chat loop.

State should track:

- pending approval id
- pending command preview
- blocked user input for replay
- status
- created_at / expires_at
- approve-next token count
- sticky dangerous-action mode flag

This state should live in terminal session scope only, not in persisted config.

### 2. Session-Aware Confirmer

`chat_cmd()` should inject a terminal session confirmer into the agent tool registry instead of using the raw default confirmer.

That confirmer should:

- allow `SAFE`
- deny `FORBIDDEN` with a clear local shell message
- for `DANGEROUS`, either:
  - allow because sticky mode is enabled
  - allow because an approve-next token exists
  - or queue a pending approval request and return `False`

This keeps tool implementations unchanged while moving approval UX to the shell layer.

### 3. Replay Model

When a request is blocked, store the original user input for the turn.

`/approve` should replay that stored user input through the same terminal session and same agent instance.

This is intentionally narrow:

- replay only the latest blocked turn
- do not attempt partial tool continuation
- do not build a generic command queue

That keeps semantics predictable and close to Telegram's current replay model.

### 4. Activity And Output UX

When approval is required, output should be compact and professional.

Example shape:

```text
approval required: dangerous action blocked
request: Edit own source: /home/comma/Documents/archon/archon/config.py
use /approve, /deny, /approve_next, or /approvals
```

Requirements:

- no extra generic rejection spam after the approval prompt
- keep the existing spinner and phase model
- do not add live panel redraws
- approval state should be visible from `/approvals`

### 5. Telegram Local-Command Fix

While implementing this slice, fix the adjacent Telegram control-flow regression:

- local Telegram shell commands must not require eager agent creation
- `/jobs`, `/job`, `/approve_next`, `/approvals`, `/approve`, and `/deny` must remain usable even if chat-agent creation would fail
- `/doctor` and other local inspection commands should degrade gracefully when the agent cannot be created

This is part of the same product principle: local control actions stay local.

## Error Handling

- If `/approve` is used with no pending request, return a clean local message.
- If a pending request expired, show that explicitly and clear it.
- If a new blocked request arrives, replace the old pending request.
- `FORBIDDEN` actions remain blocked and are never placed in the pending approval queue.
- If replayed input blocks again, update the pending request cleanly without duplicate shell noise.

## Testing Strategy

Add focused tests for:

1. terminal local approval commands are handled without model turns
2. dangerous terminal actions create pending approval state
3. `/approve` replays the latest blocked user turn
4. `/deny` clears the pending request
5. `/approve_next` allows exactly one dangerous action
6. `/approvals on|off` toggles sticky approval mode
7. duplicate blocked-action messages are suppressed in terminal chat
8. Telegram local commands stay usable when agent creation fails

## Rollout

Ship this as a small, local-first shell improvement.

The success bar is not visual flash. The success bar is that approvals feel native, professional, and easy to reason about while Archon stays lightweight.
