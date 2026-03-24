# Telegram Reliability Hardening Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make Telegram a trustworthy Archon control surface by locking shared adapter state and formalizing poll/session continuity behavior.

**Architecture:** Keep the current long-poll adapter, but add a single adapter-state lock and a small transport-health state model. Protect agent/session/approval/batching state under that lock, keep I/O outside the lock, and surface degraded transport state intentionally.

**Tech Stack:** Python, existing Telegram adapter/client, approval helpers, batching renderer, pytest

---

### Task 1: Add failing reliability regressions for Telegram state and health

**Files:**
- Modify: `tests/test_telegram_adapter.py`

**Step 1: Add transport-health regressions**
- add a test that starts from a fresh adapter, injects a startup-sync failure, and requires the adapter health snapshot to become `degraded`
- add a test that drives the existing `HTTP 409` conflict path and requires the health snapshot to become `disabled_conflict`
- add a test that clears the error path with a successful poll and requires the adapter health snapshot to return to `healthy`

**Step 2: Add concurrent chat-agent lifecycle regression**
- add a test that calls `_get_or_create_chat_agent(99)` from multiple threads
- assert that every caller receives the same agent instance and that `_session_to_chat` contains only one stable mapping for that session

**Step 3: Add cleanup regression for batched collectors**
- add a test that seeds `_batch_collectors` with a fake collector and verifies `stop()` or reset cleanup pops state safely and calls `cancel()` exactly once

**Step 4: Add degraded Telegram `/status` regression**
- add a test that marks the adapter as `degraded`, sends `/status`, and requires the reply to include a one-line transport-health prefix before the existing local status text

**Step 5: Run the focused failing slice**

Run:
```bash
python -m pytest tests/test_telegram_adapter.py -q -k 'health or conflict or concurrent or batch or degraded'
```

### Task 2: Introduce adapter state lock and transport-health helpers

**Files:**
- Modify: `archon/adapters/telegram.py`
- Modify: `tests/test_telegram_adapter.py`

**Step 1: Add adapter-owned state lock**
- add `self._state_lock = threading.RLock()` in `TelegramAdapter.__init__`
- treat these as adapter-owned shared state:
  - `_agents`
  - `_history_session_ids`
  - `_session_to_chat`
  - `_batch_collectors`
  - `_approval_always_on_chats`
  - `_approval_elevated_until`
  - `_approve_next_tokens`
  - `_pending_approvals`
  - `_active_replay_approval_ids`
  - `_current_request_ctx`

**Step 2: Add transport-health model**
- add small helpers like:
  - `_set_transport_health(state, *, error=None, source='')`
  - `_transport_health_snapshot()`
  - `_transport_health_text()`
- track:
  - `state`
  - `last_error`
  - `last_error_source`
  - `last_error_at`

**Step 3: Wire health updates into polling paths**
- set `healthy` after successful startup sync and successful poll retrieval
- set `degraded` on transient startup sync and poll failures
- set `disabled_conflict` on the existing 409 conflict path
- keep the current single terminal notice for 409 conflicts

**Step 4: Run focused verification**

Run:
```bash
python -m pytest tests/test_telegram_adapter.py -q -k 'health or conflict or startup_sync'
```

### Task 3: Guard agent, approval, request, and batching state with the adapter lock

**Files:**
- Modify: `archon/adapters/telegram.py`
- Modify: `tests/test_telegram_adapter.py`

**Step 1: Lock chat-agent/session lifecycle**
- guard `_history_session_id()` and `_get_or_create_chat_agent()` with the adapter lock
- ensure session-id creation and `_session_to_chat` updates happen atomically

**Step 2: Lock approval state transitions**
- guard helpers that read/write:
  - `_pending_approvals`
  - `_approve_next_tokens`
  - `_approval_elevated_until`
  - `_active_replay_approval_ids`
- keep approval semantics unchanged

**Step 3: Lock current-request context and collector-map mutation**
- guard installation/removal of `_current_request_ctx`
- guard `_batch_collectors` create/pop paths
- pop or snapshot collectors under the adapter lock, then flush/cancel after releasing it

**Step 4: Audit reset/stop cleanup**
- make `/reset` and `stop()` remove owned state under the adapter lock
- ensure collector `cancel()` calls happen outside the lock

**Step 5: Run focused verification**

Run:
```bash
python -m pytest tests/test_telegram_adapter.py -q -k 'approve or approvals or reset or batch or concurrent'
```

### Task 4: Surface degraded transport state in Telegram operator flows

**Files:**
- Modify: `archon/adapters/telegram.py`
- Modify: `tests/test_telegram_adapter.py`

**Step 1: Add compact transport-status prefixing**
- when Telegram transport is not `healthy`, prefix Telegram `/status` replies with a short line like:
  - `Telegram transport: degraded | source=poll | error=...`
  - or `Telegram transport: disabled_conflict`
- do not add extra noise to healthy replies

**Step 2: Keep `/help` and startup wording coherent**
- if helpful, add one short degraded hint to `/help` or `/start`, but only when the transport is not healthy
- do not change the general command taxonomy in this phase

**Step 3: Preserve degraded local-command behavior**
- keep existing local fallback behavior for `/status`, `/jobs`, `/job`, and related shell-style commands
- transport-health prefixing should wrap the existing response, not replace it

**Step 4: Run focused verification**

Run:
```bash
python -m pytest tests/test_telegram_adapter.py -q -k 'status or help or degraded or fallback'
```

### Task 5: Run full verification and manual smoke guidance

**Files:**
- Modify: `tests/test_telegram_adapter.py` if final assertions need adjustment

**Step 1: Run Telegram-focused suite**

Run:
```bash
python -m pytest tests/test_telegram_adapter.py -q
```

**Step 2: Run broader regression slice**

Run:
```bash
python -m pytest tests/test_cli.py tests/test_agent.py tests/test_telegram_adapter.py -q
```

**Step 3: Run full automated suite**

Run:
```bash
python -m pytest tests -q
```

**Step 4: Manual smoke check**
- start `archon chat` with Telegram enabled
- confirm normal startup still works
- verify a transient poll failure only degrades state instead of disabling the adapter
- verify a real 409 conflict produces one clear disable notice
- verify `/status` in Telegram includes transport state only when degraded
- verify approvals, `/activity`, and batched tool output still behave normally

