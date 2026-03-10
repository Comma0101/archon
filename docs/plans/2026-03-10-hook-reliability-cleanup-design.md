# Hook Reliability Cleanup Design

## Goal
Remove the remaining global hook-bus seam in worker completion events and make hook handler failures observable without changing user-facing behavior or making hooks part of the critical path.

## Current Problems
- `archon/workers/session_store.py` still uses a function-level `_hook_bus` attachment for worker completion events. That is global mutable state and can be overwritten by concurrent agents or worker contexts.
- `archon/control/hooks.py` catches handler exceptions silently, so broken hook subscribers are invisible in diagnosis.

## Recommended Approach
Use explicit hook-bus plumbing for worker completion emission and keep hooks best-effort, but record handler exceptions in a lightweight observable channel.

This keeps the cleanup small:
- no async/event-framework redesign
- no unregister API
- no product-surface changes
- no change to the rule that hook failures must never break execution

## Architecture
### 1. Explicit Worker Hook Emission
Worker completion events should only emit through an explicitly passed `HookBus`.

Implementation shape:
- update the worker completion helper in `archon/workers/session_store.py` to accept `hook_bus=None`
- remove all reads of `_emit_job_completed_event._hook_bus`
- thread the caller’s real bus through the existing completion path

### 2. Observable Hook Failures
`HookBus.emit()` should continue isolating handler failures, but should no longer swallow them silently.

Minimal behavior:
- catch exceptions per handler
- append a small diagnostic record to an internal in-memory list on the bus
- expose a lightweight accessor for tests and diagnostics
- optionally write a short stderr line for visibility

The internal record should contain:
- hook kind
- handler repr/name
- exception type
- exception message

This is enough to diagnose broken subscribers without turning hooks into a reporting subsystem.

## Error Handling
- Hook failures remain non-fatal.
- Worker completion with `hook_bus=None` should simply skip emission.
- Diagnostics must never raise from the failure-reporting path.

## Testing
1. Worker completion emits via explicit hook bus.
2. No global `_hook_bus` mutation is required.
3. A failing hook handler does not block later handlers.
4. Hook failures are recorded in diagnostics.

## Non-Goals
- async hooks
- hook unregister semantics
- persistent hook error storage
- hook retry logic
