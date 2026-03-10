# Terminal Input Redraw Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make terminal activity redraws restore the correct visible input state during first-character capture and live slash mode.

**Architecture:** Add one shared transient input-state bridge between the input loop and `TerminalActivityFeed`. Update the raw/live input helpers to publish visible query state while the terminal is outside stable readline ownership.

**Tech Stack:** Python, readline, termios/tty, pytest

---

### Task 1: Add failing redraw tests

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `tests/test_slash_palette.py`

**Step 1: Write failing tests**
- add a test that simulates an activity event arriving after the first raw non-slash character is captured but before readline owns the visible line
- add a test that simulates the live slash palette updating query text and verifies the shared visible input state is updated/cleared

**Step 2: Run tests to verify failure**

Run:
```bash
python -m pytest tests/test_cli.py tests/test_slash_palette.py -q -k 'redraw or first_character or query_state'
```

### Task 2: Publish transient visible input state

**Files:**
- Modify: `archon/slash_palette.py`
- Modify: `archon/cli_interactive_commands.py`

**Step 1: Implement minimal shared-state callbacks**
- let `read_interactive_input()` accept an optional visible-input callback
- publish first-character state before readline startup hook completes
- publish slash query state on every live palette mutation
- clear the shared state on exit paths

**Step 2: Re-run targeted tests**

Run:
```bash
python -m pytest tests/test_cli.py tests/test_slash_palette.py -q -k 'redraw or first_character or query_state'
```

### Task 3: Verify no regressions

**Files:**
- Modify: `AGENT_CONTEXT.json`

**Step 1: Run focused shell tests**

Run:
```bash
python -m pytest tests/test_cli.py tests/test_slash_palette.py tests/test_terminal_feed.py -q
```

**Step 2: Run full suite**

Run:
```bash
XDG_STATE_HOME=/tmp/archon-state python -m pytest tests -q
```

