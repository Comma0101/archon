# Live Slash Palette Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the remaining legacy slash behavior with a live inline command palette that appears immediately when the user types `/`.

**Architecture:** Add a dedicated slash-only input path that uses a small raw-key palette module and resolves back into the existing command handlers. Keep normal chat input on the existing path to limit regressions.

**Tech Stack:** Python, readline/termios/tty/select, pytest

---

### Task 1: Add failing slash-palette tests

**Files:**
- Create: `tests/test_slash_palette.py`
- Modify: `tests/test_cli.py`

**Step 1: Write the failing tests**

Add tests for:
- palette activates immediately on `/`
- live filtering narrows results as text grows
- `Enter` resolves the highlighted command
- non-slash input stays on the normal path

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_slash_palette.py tests/test_cli.py -q -k slash`

**Step 3: Write minimal implementation**

Create the slash palette module and wire it into the interactive chat loop.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_slash_palette.py tests/test_cli.py -q -k slash`

**Step 5: Run broader verification**

Run:
- `python -m pytest tests/test_slash_palette.py tests/test_cli.py tests/test_terminal_feed.py -q`
- `XDG_STATE_HOME=/tmp/archon-state python -m pytest tests -q`
