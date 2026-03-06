# Telegram Shell Parity Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Bring Telegram to parity with the premium terminal shell for core local inspection commands without consuming model turns.

**Architecture:** Reuse the existing compact local command handlers from `archon/cli_repl_commands.py` and route Telegram commands through them in `TelegramAdapter`, keeping Telegram a thin transport surface rather than a separate command system.

**Tech Stack:** Python 3.11+, pytest, `archon/adapters/telegram.py`, `archon/cli_repl_commands.py`, existing Telegram adapter tests.

---

### Task 1: Add Telegram Tests For Local Shell-Parity Commands

**Files:**
- Modify: `tests/test_telegram_adapter.py`

**Step 1: Write the failing tests**

Add focused adapter tests for:

- `/status` handled locally
- `/cost` handled locally
- `/doctor` handled locally
- `/permissions` handled locally
- `/skills` handled locally
- `/plugins` handled locally
- `/mcp` handled locally
- `/profile` handled locally

Each test should prove:

- the adapter sends the local summary reply
- `agent.run()` is not called
- history/session save behavior remains consistent with existing Telegram command handling

**Step 2: Run targeted tests to verify they fail**

Run:
```bash
python -m pytest tests/test_telegram_adapter.py -q -k "status or cost or doctor or permissions or skills or plugins or mcp or profile"
```

Expected: FAIL because Telegram does not route these commands yet.

**Step 3: Confirm failure mode**

Expect missing command routing in `TelegramAdapter._handle_message`, not unrelated setup failures.

### Task 2: Route Telegram Commands Through Existing Local Handlers

**Files:**
- Modify: `archon/adapters/telegram.py`
- Modify: `tests/test_telegram_adapter.py`

**Step 1: Write minimal implementation**

- import the needed handler functions from `archon.cli_repl_commands`
- detect supported Telegram local commands in `_handle_message`
- route them through the same handler path used by terminal-style summaries
- send the returned text with `_send_text_and_record`
- ensure command handling remains local and never falls through to `agent.run()`

**Step 2: Run targeted tests**

Run:
```bash
python -m pytest tests/test_telegram_adapter.py -q -k "status or cost or doctor or permissions or skills or plugins or mcp or profile"
```

Expected: PASS.

### Task 3: Add One Regression Test For Normal Chat Fallback

**Files:**
- Modify: `tests/test_telegram_adapter.py`

**Step 1: Write one regression test**

Add a test proving that a normal non-command Telegram message still goes through `agent.run()` after the new command routing changes.

**Step 2: Run the focused test to verify it fails if needed**

Run:
```bash
python -m pytest tests/test_telegram_adapter.py -q -k "normal_chat"
```

Expected: PASS or FAIL depending on existing coverage. If already covered, keep the new regression minimal and move on.

**Step 3: Keep only the minimal necessary code**

Do not widen routing beyond the planned command set.

### Task 4: Verification And Context Sync

**Files:**
- Modify: `AGENT_CONTEXT.json`

**Step 1: Run relevant verification**

Run:
```bash
python -m pytest tests/test_telegram_adapter.py -q
python -m pytest tests/test_cli.py tests/test_telegram_adapter.py tests/test_agent.py tests/test_config.py tests/test_mcp.py -q
python -m pytest tests -q
```

Expected: all pass.

**Step 2: Update context log**

Add an `AGENT_CONTEXT.json` entry describing the Telegram shell-parity slice and verification results.

**Step 3: Validate JSON formatting**

Run:
```bash
python -m json.tool AGENT_CONTEXT.json >/dev/null
```

Expected: exit 0.

**Step 4: Commit**

Run:
```bash
git add archon/adapters/telegram.py tests/test_telegram_adapter.py AGENT_CONTEXT.json docs/plans/2026-03-06-telegram-shell-parity-design.md docs/plans/2026-03-06-telegram-shell-parity-plan.md
git commit -m "feat: add telegram shell command parity"
```

Expected: clean commit for the slice.
