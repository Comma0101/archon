# Telegram Reliability Hardening Design

**Date:** 2026-03-23
**Branch:** `master`
**Status:** Approved for implementation

## Problem

Telegram is becoming a primary Archon control surface, but the adapter still has reliability risks that are larger than its remaining feature gaps.

The current issues are structural:
- shared mutable adapter state is spread across many dicts and sets in [telegram.py](/home/comma/Documents/archon/archon/adapters/telegram.py) with no single synchronization boundary
- the adapter mixes polling, per-chat agent lifecycle, approval state, request context, and batched tool output in one class
- timer-driven batching and the polling loop can both touch adapter-owned state, which makes concurrent state transitions harder to reason about
- poll-loop failures are partially visible in terminal logs, but transport health is not modeled explicitly enough to drive consistent behavior

The result is a Telegram surface that mostly works, but is harder to trust under conflict, reconnect, and multi-step approval flows than it should be.

## Goals

- Make Telegram adapter state transitions deterministic under concurrent access.
- Add an explicit transport-health model for polling continuity.
- Keep agent/session/approval/batching state consistent across poll-loop failures and shutdown.
- Improve operator visibility for degraded or disabled Telegram transport without adding a new service boundary.
- Stay lightweight and keep the existing long-poll architecture.

## Non-Goals

- Moving to webhooks or a separate Telegram service.
- Adding broad send/edit retry semantics in this phase.
- Rewriting the adapter into many new modules before the correctness issues are fixed.
- Changing core approval semantics.
- Adding heavy rendering dependencies or transport-specific UI chrome.

## Chosen Approach

Use a **single adapter-state lock plus explicit transport health state**.

The adapter will keep its current top-level shape, but it will stop treating shared mutable state as implicitly single-threaded. State mutation will move behind one small synchronization boundary, while network I/O and rendering stay outside the lock whenever possible.

At the same time, polling continuity will be modeled explicitly with a small transport-health state machine instead of relying only on ad hoc stderr messages.

## Architecture

### 1. Adapter-owned state boundary

The Telegram adapter owns several categories of mutable state:
- chat agents and history session ids
- session-to-chat routing
- batch collectors
- approval maps and elevated mode state
- per-request context for the currently executing Telegram request

These structures should be treated as one shared state domain and protected by a single adapter-level `threading.RLock`.

Why `RLock` instead of `Lock`:
- several helper methods call one another while touching the same state
- this phase should improve correctness without forcing a large helper-graph rewrite first

Rule:
- snapshot or mutate adapter-owned state under the lock
- perform network calls, model calls, and collector flush work outside the lock

That keeps lock scope narrow while making state transitions coherent.

### 2. Explicit transport health state

The adapter should maintain a compact health model with states like:
- `healthy`
- `degraded`
- `disabled_conflict`

Tracked fields should include:
- current state
- last error type/message
- last error timestamp
- whether polling was disabled due to a 409 conflict

This is not a new subsystem. It is a small internal status model used to:
- make logging consistent
- let status surfaces describe transport condition
- make conflict/disconnect behavior easier to test

### 3. Lock the high-risk state domains first

Not every line in the adapter needs locking. The high-risk paths are:
- `_get_or_create_chat_agent()`
- history-session creation and session-to-chat routing
- pending approval lifecycle
- approve-next and elevated-approval token tracking
- current request context installation/removal
- batch collector map creation/removal
- reset/shutdown cleanup

The important design constraint is not “lock everything forever.” It is:
- all adapter-owned shared structures must have one clear ownership rule

### 4. Keep flush/cancel work outside the state lock

Batch collectors already have their own internal locking in [telegram_renderer.py](/home/comma/Documents/archon/archon/ux/telegram_renderer.py). The adapter should:
- pop or snapshot collectors under the adapter lock
- flush or cancel them after releasing the adapter lock

The same principle applies to any future send/edit retry work:
- decide ownership under the lock
- do I/O after the lock is released

### 5. Surface degraded transport state intentionally

If Telegram transport is degraded but still functioning, the operator should be able to tell.

This phase should add lightweight visibility, not a new UI:
- terminal stderr remains the source for startup/conflict notices
- Telegram `/status` responses should include transport state when it is not `healthy`
- fallback/degraded wording should be consistent with current state-first UX

If polling is fully disabled due to conflict, the terminal process should emit one clear message and stop spamming repeated errors.

## Risks

### Risk 1: Holding the lock across network or model calls
Mitigation: explicitly separate state snapshot/mutation from I/O work.

### Risk 2: Locking only some state paths
Mitigation: define the adapter-owned state domain first and audit helpers against it.

### Risk 3: Accidentally changing approval behavior while adding locks
Mitigation: treat approval semantics as frozen; only guard existing transitions.

### Risk 4: Health state becomes noisy instead of useful
Mitigation: surface health only when degraded or disabled; keep `healthy` mostly implicit.

## Expected Outcome

After this phase:
- Telegram polling and shutdown behavior should be more coherent under failure
- per-chat agent, approval, and batching state should be safer under concurrent access
- 409 conflict and transient poll issues should have explicit, testable state transitions
- the adapter will still be large, but its most important correctness boundary will be clearer

