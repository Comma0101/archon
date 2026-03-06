# Telegram Shell Parity Design

## Decision

Bring Telegram to near-parity with the new terminal control surface by adding compact local inspection commands, not by building a more stateful Telegram workflow layer first.

This keeps terminal and Telegram equal-weight operator surfaces while preserving Archon's lightweight architecture.

## Why This Direction

Terminal now has a stronger local control surface:

- `/status`
- `/cost`
- `/doctor`
- `/permissions`
- `/skills`
- `/plugins`
- `/mcp`
- `/profile`
- `/jobs`
- `/job`
- `/compact`
- `/context`

Telegram already has the harder async pieces:

- approval callbacks and replay
- route-progress notices
- `/jobs` and `/job`
- per-chat session state

So the highest-value next slice is command parity, not more approval machinery.

## Scope

Add Telegram support for the highest-value local control commands that already exist in terminal and already return compact deterministic text.

In scope:

- `/status`
- `/cost`
- `/doctor`
- `/permissions`
- `/skills`
- `/plugins`
- `/mcp`
- `/profile`

Out of scope for this slice:

- redesigning Telegram approvals
- richer progress streaming
- Telegram-specific dashboards or menus
- voice UX changes
- background push notifications beyond current behavior

## Architecture

Reuse the existing local command handlers in `archon/cli_repl_commands.py` wherever possible.

Telegram should remain a thin transport surface:

1. recognize supported control commands in `TelegramAdapter`
2. route them to the same local summary functions used by terminal REPL
3. send the returned compact text back to Telegram
4. record the exchange in Telegram session history without consuming an LLM model turn

This avoids duplicated business logic and keeps Telegram command semantics aligned with terminal semantics.

## Command Strategy

Recommended supported commands for this slice:

- `/status`
- `/cost`
- `/doctor`
- `/permissions`
- `/skills`
- `/skills show <name>`
- `/plugins`
- `/plugins show <name>`
- `/mcp`
- `/mcp servers`
- `/mcp show <server>`
- `/profile`
- `/profile show`

Not recommended for this slice:

- `/compact`
- `/context`

Reason: those are more terminal-oriented session controls and less obviously useful in Telegram. They can come later if needed.

## UX Rules

- local Telegram control commands must not call `agent.run()`
- replies must stay compact and message-safe
- command behavior should match terminal outputs closely enough that users can rely on the same mental model
- unknown commands should continue to fall through to normal chat behavior
- approval flow must remain unchanged unless parity work exposes a real regression

## Testing

Add focused adapter tests to prove:

- each supported Telegram command is handled locally
- no model turn occurs for those commands
- existing `/jobs`, `/job`, approval callback, and normal chat behavior stay intact

Testing should stay at the adapter layer, not by re-testing all terminal command formatting logic.

## Success Criteria

Telegram can now inspect runtime state, policy state, skill/plugin state, MCP state, and active profile using local commands with no model turn.

That makes Telegram meaningfully closer to terminal in day-to-day operator use, while keeping the implementation small and low-risk.
