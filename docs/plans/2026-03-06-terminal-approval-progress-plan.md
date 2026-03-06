# Terminal Approval And Progress UX Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace raw terminal dangerous-action prompts with a professional session-scoped approval shell flow, and fix adjacent Telegram local-command control flow so local commands stay local.

**Architecture:** Keep the current text-first shell and spinner model. Add a small terminal-session approval state plus local approval commands, then wire a session-aware confirmer into interactive chat. In the same slice, decouple Telegram local control commands from eager agent construction so inspection commands still work when chat-agent creation fails.

**Tech Stack:** Python 3.11+, pytest, existing `archon/cli_*` modules, `archon/safety.py`, `archon/adapters/telegram.py`, and current tool registry confirmer hooks.

---

### Task 1: Add Terminal Approval Command Surface

**Files:**
- Modify: `archon/cli_commands.py`
- Modify: `archon/cli_repl_commands.py`
- Modify: `tests/test_cli.py`

**Step 1: Write the failing test**

Add tests proving these local commands exist and do not require model turns:

```python
def test_slash_commands_include_terminal_approval_commands():
    names = {name for name, _desc in _SLASH_COMMANDS}
    assert {"/approvals", "/approve", "/deny", "/approve_next"} <= names


def test_handle_approvals_command_reports_default_state():
    agent = SimpleNamespace()
    handled, msg = _handle_repl_command(agent, "/approvals")
    assert handled == "approvals"
    assert "dangerous_mode" in msg
```

**Step 2: Run test to verify it fails**

Run:

```bash
python -m pytest tests/test_cli.py -q -k "approvals or approve_next or deny"
```

Expected: FAIL because terminal approval commands do not exist yet.

**Step 3: Write minimal implementation**

- add `/approvals`, `/approve`, `/deny`, `/approve_next` to `SLASH_COMMAND_GROUPS`
- add any needed slash subvalues for `/approvals on|off`
- add REPL handlers in `archon/cli_repl_commands.py`
- keep handler output compact and deterministic

**Step 4: Run test to verify it passes**

Run:

```bash
python -m pytest tests/test_cli.py -q -k "approvals or approve_next or deny"
```

Expected: PASS.

**Step 5: Commit**

```bash
git add archon/cli_commands.py archon/cli_repl_commands.py tests/test_cli.py
git commit -m "feat: add terminal approval shell commands"
```

### Task 2: Add Session Approval State To Interactive Chat

**Files:**
- Modify: `archon/cli_interactive_commands.py`
- Modify: `archon/cli_ui.py`
- Modify: `tests/test_cli.py`

**Step 1: Write the failing test**

Add tests proving a dangerous action in terminal chat creates a pending approval state and emits one clean approval-required message instead of raw `y/N` confirmation.

```python
def test_terminal_dangerous_action_creates_pending_approval_state(...):
    ...
    assert "approval required" in rendered_output
    assert pending["status"] == "pending"
```

**Step 2: Run test to verify it fails**

Run:

```bash
python -m pytest tests/test_cli.py -q -k "pending approval or approval required"
```

Expected: FAIL because terminal interactive chat still uses the raw confirmer path.

**Step 3: Write minimal implementation**

- add a small terminal-session approval state structure in `chat_cmd()`
- implement a session-aware confirmer for interactive terminal chat
- replace raw `safety.confirm()` usage in this path only
- add a compact formatter in `cli_ui.py` for terminal approval-required output
- keep only one pending approval per session; newest request replaces the old one

**Step 4: Run test to verify it passes**

Run:

```bash
python -m pytest tests/test_cli.py -q -k "pending approval or approval required"
```

Expected: PASS.

**Step 5: Commit**

```bash
git add archon/cli_interactive_commands.py archon/cli_ui.py tests/test_cli.py
git commit -m "feat: add terminal session approval state"
```

### Task 3: Implement Replay, Deny, And Sticky Approval Behavior

**Files:**
- Modify: `archon/cli_interactive_commands.py`
- Modify: `archon/cli_repl_commands.py`
- Modify: `tests/test_cli.py`

**Step 1: Write the failing test**

Add tests for:

```python
def test_approve_replays_latest_blocked_turn(...):
    ...
    assert replayed_inputs == ["edit that file"]


def test_deny_clears_pending_terminal_request(...):
    ...
    assert "denied" in output.lower()


def test_approve_next_allows_one_dangerous_action(...):
    ...
    assert first_allowed is True
    assert second_allowed is False


def test_approvals_on_off_toggles_sticky_terminal_mode(...):
    ...
    assert "enabled" in on_msg
    assert "disabled" in off_msg
```

**Step 2: Run test to verify it fails**

Run:

```bash
python -m pytest tests/test_cli.py -q -k "replays latest blocked turn or approve_next or approvals on off or deny clears"
```

Expected: FAIL because replay and terminal approval mode behavior do not exist yet.

**Step 3: Write minimal implementation**

- wire `/approve`, `/deny`, `/approve_next`, and `/approvals on|off` into the terminal session state
- replay the stored blocked user input through the same agent/session
- expire or clear pending state after replay/deny as appropriate
- keep duplicate blocked-action output suppressed

**Step 4: Run test to verify it passes**

Run:

```bash
python -m pytest tests/test_cli.py -q -k "replays latest blocked turn or approve_next or approvals on off or deny clears"
```

Expected: PASS.

**Step 5: Commit**

```bash
git add archon/cli_interactive_commands.py archon/cli_repl_commands.py tests/test_cli.py
git commit -m "feat: add terminal approval replay flow"
```

### Task 4: Tighten Activity Messaging Around Approval States

**Files:**
- Modify: `archon/cli_ui.py`
- Modify: `archon/cli_interactive_commands.py`
- Modify: `tests/test_cli.py`

**Step 1: Write the failing test**

Add tests proving:

```python
def test_terminal_blocked_action_suppresses_duplicate_generic_rejection(...):
    ...
    assert rendered.count("approval required") == 1
    assert "blocked by safety policy" not in rendered.lower()


def test_approvals_command_shows_pending_request_preview(...):
    ...
    assert "request:" in msg
```

**Step 2: Run test to verify it fails**

Run:

```bash
python -m pytest tests/test_cli.py -q -k "duplicate generic rejection or pending request preview"
```

Expected: FAIL because approval output is not yet fully normalized.

**Step 3: Write minimal implementation**

- normalize terminal approval-required text into one compact shell message
- ensure `/approvals` shows the pending request preview, mode, and token state
- preserve the existing spinner and turn-stats model without adding a live panel

**Step 4: Run test to verify it passes**

Run:

```bash
python -m pytest tests/test_cli.py -q -k "duplicate generic rejection or pending request preview"
```

Expected: PASS.

**Step 5: Commit**

```bash
git add archon/cli_ui.py archon/cli_interactive_commands.py tests/test_cli.py
git commit -m "fix: polish terminal approval messaging"
```

### Task 5: Fix Telegram Local Commands To Stay Local

**Files:**
- Modify: `archon/adapters/telegram.py`
- Modify: `tests/test_telegram_adapter.py`

**Step 1: Write the failing test**

Add tests proving local Telegram commands still work when chat-agent creation fails:

```python
def test_jobs_command_does_not_require_agent_creation(monkeypatch):
    ...
    assert "worker:sess-1" in sent[0][1]


def test_doctor_command_degrades_gracefully_when_agent_creation_fails(monkeypatch):
    ...
    assert "missing" in sent[0][1] or "unavailable" in sent[0][1]
```

**Step 2: Run test to verify it fails**

Run:

```bash
python -m pytest tests/test_telegram_adapter.py -q -k "does_not_require_agent_creation or degrades_gracefully"
```

Expected: FAIL because `_handle_local_shell_command()` currently forces eager agent creation.

**Step 3: Write minimal implementation**

- route truly local Telegram commands before any chat-agent creation
- make inspection commands degrade cleanly if an agent cannot be constructed
- preserve current behavior for normal chat and approval replay paths

**Step 4: Run test to verify it passes**

Run:

```bash
python -m pytest tests/test_telegram_adapter.py -q -k "does_not_require_agent_creation or degrades_gracefully"
```

Expected: PASS.

**Step 5: Commit**

```bash
git add archon/adapters/telegram.py tests/test_telegram_adapter.py
git commit -m "fix: keep telegram local commands agent-independent"
```

### Task 6: Run Focused Regression And Full Verification

**Files:**
- Modify: `AGENT_CONTEXT.json`

**Step 1: Update context log**

Add a concise entry describing:

- terminal session approval shell flow
- compact approval messaging
- Telegram local-command control-flow fix

**Step 2: Run focused regression suites**

Run:

```bash
python -m pytest tests/test_cli.py tests/test_telegram_adapter.py -q
```

Expected: PASS.

**Step 3: Run full verification**

Run:

```bash
python -m pytest tests -q
```

Expected: PASS.

**Step 4: Commit**

```bash
git add AGENT_CONTEXT.json
git commit -m "docs: record terminal approval progress rollout"
```
