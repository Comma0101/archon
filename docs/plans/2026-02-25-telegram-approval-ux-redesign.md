# Telegram Approval UX Redesign Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Redesign Telegram dangerous-action approvals to feel smooth by using request-scoped pending approvals with inline buttons and automatic request replay.

**Architecture:** Keep approvals localized to `TelegramAdapter` with a small per-chat pending approval state. Extend `TelegramBotClient` with callback/query and message-edit helpers, then intercept blocked tool attempts in the adapter so Telegram shows a single approval prompt instead of duplicate messages.

**Tech Stack:** Python stdlib (`urllib`, `json`, `dataclasses`/dict state), Telegram Bot API long polling/callback queries, existing Archon Telegram adapter/client/tests.

---

### Task 1: Plan + test coverage for Telegram callback approvals

**Files:**
- Modify: `tests/test_telegram_adapter.py`
- Modify: `tests/test_telegram_client.py`

**Step 1: Write failing tests**
- Add Telegram adapter tests for:
  - blocked dangerous action creates pending approval prompt and suppresses duplicate final reply
  - `/approve` replays the pending request
  - callback query approve/deny routes and answers callback query
- Add Telegram client tests for callback/edit helper API payloads.

**Step 2: Run targeted tests to verify failures**

Run:
```bash
pytest tests/test_telegram_adapter.py tests/test_telegram_client.py -q
```

**Step 3: Confirm failure mode**
- Expect missing methods / unsupported callback_query handling / old duplicate behavior assertions to fail.

### Task 2: Telegram client primitives for smooth approval UX

**Files:**
- Modify: `archon/adapters/telegram_client.py`
- Test: `tests/test_telegram_client.py`

**Step 1: Add minimal helpers**
- `answer_callback_query(...)`
- `edit_message_text(...)`
- `edit_message_reply_markup(...)` (optional if needed)
- `send_text(..., reply_markup=...)` support with backward-compatible defaults

**Step 2: Run targeted client tests**

Run:
```bash
pytest tests/test_telegram_client.py -q
```

### Task 3: Request-scoped approval state + callback handling in Telegram adapter

**Files:**
- Modify: `archon/adapters/telegram.py`
- Test: `tests/test_telegram_adapter.py`

**Step 1: Add per-chat pending approval state**
- Store one pending item per chat:
  - request ID
  - original user message
  - blocked command preview
  - timestamps / expiry
  - approval prompt message id

**Step 2: Handle callback queries**
- Accept `callback_query` updates in polling
- Parse callback data for approve/deny/allow-ttl actions
- `answerCallbackQuery` always
- Edit approval prompt status text/buttons

**Step 3: Fix duplicate-message behavior**
- When dangerous action is blocked and approval prompt is issued, suppress the extra “Command rejected by safety gate” Telegram reply for that turn.

**Step 4: Implement `/approve` + `/deny` fallbacks**
- `/approve` approves latest pending request and auto-replays the original message
- `/deny` clears pending request
- Keep `/approve_next` compatibility but de-emphasize it

**Step 5: Implement replay/elevated execution scopes**
- Request-scoped replay allowance (for the replayed message)
- Optional chat elevated TTL (e.g. 15m) via callback button or command
- `FORBIDDEN` remains blocked

**Step 6: Run targeted adapter tests**

Run:
```bash
pytest tests/test_telegram_adapter.py -q
```

### Task 4: Regression verification and context update

**Files:**
- Modify: `AGENT_CONTEXT.json`

**Step 1: Run full test suite**

Run:
```bash
pytest tests/ -q
```

**Step 2: Update project context**
- Record Telegram approval UX redesign (request-scoped callbacks + replay)
- Update test count

**Step 3: Validate context JSON**

Run:
```bash
python -m json.tool AGENT_CONTEXT.json >/dev/null
```
