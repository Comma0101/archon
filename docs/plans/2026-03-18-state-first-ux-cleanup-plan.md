# State-First UX Cleanup Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reduce operator confusion by making Archon's CLI and Telegram responses state-first, compact, and consistent.

**Architecture:** Reuse the current local command handlers, approval helpers, context snapshot, and activity feed. Add one lightweight shared operator-text layer so command results, blocked actions, and pressure guidance use the same vocabulary across CLI and Telegram without introducing terminal-only UI complexity.

**Tech Stack:** Python, existing CLI/Telegram adapters, `context_metrics`, approval helpers, pytest

---

### Task 1: Add failing UX regression tests for state clarity

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `tests/test_telegram_adapter.py`

**Step 1: Add `/compact` clarity regressions**
- add a CLI unit test that fails if `/compact` echoes an arbitrary compaction summary instead of confirming queued compacted context
- add an interactive CLI test that verifies `/compact` output mentions `pending_compactions` and `next_turn=uses_compacted_context`

**Step 2: Add approval-state regressions**
- add CLI tests for blocked-action feedback that require the response to identify the pending request and replay path
- add Telegram tests that require the same approval meaning and recovery language

**Step 3: Add parity regressions for help and context guidance**
- add tests that require `/help` to group commands by operator workflow rather than only listing them
- add tests that require `/status` and `/context` to surface actionable guidance only when pressure is elevated

**Step 4: Run the failing slice**

Run:
```bash
python -m pytest tests/test_cli.py tests/test_telegram_adapter.py -q -k 'compact or approve or approvals or help or context or status'
```

### Task 2: Introduce a shared operator-text helper

**Files:**
- Create: `archon/ux/operator_messages.py`
- Modify: `archon/cli_repl_commands.py`
- Modify: `archon/adapters/telegram.py`
- Modify: `archon/adapters/telegram_approvals.py`

**Step 1: Add small shared formatting helpers**
- create functions that build compact state-first messages for:
  - compaction success
  - fresh-chat reset
  - context/status recommendations
  - blocked-action and approval status

**Step 2: Keep helpers state-only**
- pass in already-known state such as `history_messages`, `pending_compactions`, `pressure`, `pending command preview`, and replay availability
- do not move command execution into the helper module

**Step 3: Rewire CLI and Telegram to use the shared text**
- replace duplicated string assembly in CLI/Telegram paths with the new helper functions
- preserve existing command routing and behavior

**Step 4: Run focused helper consumers**

Run:
```bash
python -m pytest tests/test_cli.py tests/test_telegram_adapter.py -q -k 'compact or approvals or help'
```

### Task 3: Normalize local command responses around state change

**Files:**
- Modify: `archon/cli_repl_commands.py`
- Modify: `archon/context_metrics.py`
- Modify: `tests/test_cli.py`

**Step 1: Tighten `/compact`, `/new`, and `/clear`**
- make `/compact` report compacted message count, artifact path, queued compaction count, and that the next turn will use compacted context
- make `/new` and `/clear` explicitly describe “fresh chat context” while keeping the same process/session running

**Step 2: Surface waiting state in `/status`**
- extend the status surface to mention whether Archon currently has:
  - pending compactions
  - a pending approval request
  - elevated pressure
- keep the line short; do not dump raw counters that belong in `/context`

**Step 3: Keep `/context` factual and actionable**
- keep detailed fields in `/context`
- add a short recommendation only when `pressure` is `warn` or `high`

**Step 4: Run local-command verification**

Run:
```bash
python -m pytest tests/test_cli.py -q -k 'compact or clear or new or status or context'
```

### Task 4: Make approvals deterministic across CLI and Telegram

**Files:**
- Modify: `archon/cli_interactive_commands.py`
- Modify: `archon/cli_repl_commands.py`
- Modify: `archon/adapters/telegram.py`
- Modify: `archon/adapters/telegram_approvals.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_telegram_adapter.py`

**Step 1: Clarify blocked-action messages**
- when a dangerous action is blocked, explicitly say:
  - blocked
  - what request is pending
  - that `/approve` replays it
  - that `/approve_next` arms one future dangerous action

**Step 2: Clarify replay/armed-state responses**
- make `/approve`, `/deny`, `/approve_next`, and `/approvals` all describe the current approval state without ambiguity
- keep wording aligned across CLI and Telegram

**Step 3: Preserve existing mechanics**
- do not change approval state transitions or replay behavior in this task
- only improve clarity and consistency of surfaced state

**Step 4: Run approval-focused tests**

Run:
```bash
python -m pytest tests/test_cli.py tests/test_telegram_adapter.py -q -k 'approve or approvals or deny or blocked or replay'
```

### Task 5: Tighten help text and command taxonomy

**Files:**
- Modify: `archon/cli_repl_commands.py`
- Modify: `archon/cli_commands.py`
- Modify: `archon/adapters/telegram.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_telegram_adapter.py`

**Step 1: Reframe `/help` around operator workflows**
- make the top sections:
  - inspect state
  - reduce pressure
  - handle blocked actions
- keep secondary commands below those primary groups

**Step 2: Align slash descriptions and Telegram metadata**
- make slash-command descriptions and Telegram bot-command descriptions reinforce the same mental model
- avoid drift in naming such as “clear conversation” vs “fresh chat context” when the distinction matters

**Step 3: Run parity-focused help tests**

Run:
```bash
python -m pytest tests/test_cli.py tests/test_telegram_adapter.py -q -k 'help or slash or bot commands or new'
```

### Task 6: Tighten default tool activity summaries

**Files:**
- Modify: `archon/agent.py`
- Modify: `tests/test_agent.py`

**Step 1: Add failing activity-summary regressions**
- add tests that require shell activity output to emphasize command, exit code, and compact excerpt instead of long raw transcripts
- add tests that guard against ambiguous “looked successful but actually failed” output

**Step 2: Keep operator output compact**
- adjust the activity-print helpers so the default feed emphasizes outcome first
- preserve the current lightweight plain-text feed and existing sanitization constraints

**Step 3: Run focused activity tests**

Run:
```bash
python -m pytest tests/test_agent.py -q -k 'tool call or tool result or shell'
```

### Task 7: Run end-to-end verification and smoke guidance

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `tests/test_telegram_adapter.py`

**Step 1: Add one operator-journey regression**
- add a synthetic CLI test for a confused operator path:
  - blocked action
  - `/approvals`
  - `/approve`
  - `/status`
  - `/compact`
  - `/context`
- assert the surfaced text is coherent at each step

**Step 2: Run the focused suite**

Run:
```bash
python -m pytest tests/test_agent.py tests/test_cli.py tests/test_telegram_adapter.py -q
```

**Step 3: Run the full automated suite**

Run:
```bash
XDG_STATE_HOME=/tmp/archon-state python -m pytest tests -q
```

**Step 4: Manual smoke check**
- start `archon chat`
- verify `/status`, `/context`, `/compact`, `/new`, `/approvals`, `/approve`, and `/approve_next`
- trigger one blocked dangerous action and verify the replay path is obvious
- send the same local commands in Telegram and verify wording parity
