# Terminal Command Surface Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make terminal slash commands easier to discover and execute by unifying picker/completion behavior, simplifying the visible command surface, and using live runtime values.

**Architecture:** Extend the existing `archon/cli_commands.py` slash metadata into a richer runtime-backed suggestion model shared by the readline completer and filtered picker path. Keep the current REPL architecture, but add filtered picker fallback on partial slash submission and fold duplicate commands into a simpler visible surface.

**Tech Stack:** Python stdlib, readline, existing Archon CLI modules, pytest.

---

### Task 1: Add failing tests for the new visible command surface

**Files:**
- Modify: `tests/test_cli.py`

**Step 1: Write failing tests**
- Add tests that assert `/model-list` and `/model-set` are no longer in the primary slash command surface.
- Add tests that assert `/model` remains and `/permissions` has actionable modes.

**Step 2: Run red tests**
Run: `python -m pytest tests/test_cli.py -q -k "model_list or permissions"`
Expected: fail on old surface assumptions.

**Step 3: Implement minimal metadata changes**
- Update slash command groups and visible command descriptions in `archon/cli_commands.py`.
- Keep handler aliases untouched for now.

**Step 4: Run tests to green**
Run the same focused test command.

**Step 5: Commit**
`git add tests/test_cli.py archon/cli_commands.py && git commit -m "feat: simplify visible slash command surface"`

### Task 2: Add failing tests for dynamic live subvalue coverage

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `archon/cli_commands.py`

**Step 1: Write failing tests**
- Add tests for `/profile set <configured-profile>`
- Add tests for `/skills show|use <all built-in skills>`
- Add tests for `/permissions <mode>`
- Add tests for `/jobs purge`
- Add tests for `/job <recent-id>` suggestions using monkeypatched job stores

**Step 2: Run red tests**
Run: `python -m pytest tests/test_cli.py -q -k "profile_set or skills_use or permissions or jobs_purge or recent_job"`

**Step 3: Implement minimal runtime-backed suggestion builders**
- Extend `build_slash_subvalues()` with live values from config, built-in skills, and recent job summaries.

**Step 4: Run tests to green**
Run the same focused test command.

**Step 5: Commit**
`git add tests/test_cli.py archon/cli_commands.py && git commit -m "feat: add live slash subvalue coverage"`

### Task 3: Add failing tests for filtered picker behavior on partial slash input

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `archon/cli_commands.py`
- Modify: `archon/cli_interactive_commands.py`

**Step 1: Write failing tests**
- Add tests showing `/m` opens a filtered picker and echoes the chosen command.
- Add tests showing `/profile set` and `/skills use` can resolve through picker selection.
- Add tests that complete commands do not trigger the picker.

**Step 2: Run red tests**
Run: `python -m pytest tests/test_cli.py -q -k "filtered_picker or partial_slash"`

**Step 3: Implement filtered picker support**
- Extend picker helpers to accept an initial slash query.
- Update chat loop to route incomplete slash input into the filtered picker path.

**Step 4: Run tests to green**
Run the same focused test command.

**Step 5: Commit**
`git add tests/test_cli.py archon/cli_commands.py archon/cli_interactive_commands.py && git commit -m "feat: add filtered slash picker resolution"`

### Task 4: Add actionable `/permissions` behavior

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `archon/cli_repl_commands.py`
- Modify: `archon/cli.py`

**Step 1: Write failing tests**
- Add tests for `/permissions auto`, `/permissions accept_reads`, `/permissions confirm_all`
- Verify status reflects the updated mode
- Decide persistence behavior and test it explicitly

**Step 2: Run red tests**
Run: `python -m pytest tests/test_cli.py -q -k "permissions"`

**Step 3: Implement minimal behavior**
- Extend `handle_permissions_command()`
- Add TOML persistence helper if the chosen behavior is persisted like `/calls`

**Step 4: Run tests to green**
Run the same focused test command.

**Step 5: Commit**
`git add tests/test_cli.py archon/cli_repl_commands.py archon/cli.py && git commit -m "feat: make permissions command actionable"`

### Task 5: Verify end-to-end terminal shell behavior

**Files:**
- Modify if needed: `tests/test_cli.py`
- Modify if needed: `AGENT_CONTEXT.json`

**Step 1: Run focused CLI suite**
Run: `python -m pytest tests/test_cli.py -q`
Expected: all pass.

**Step 2: Run full suite**
Run: `XDG_STATE_HOME=/tmp/archon-state python -m pytest tests -q`
Expected: all pass.

**Step 3: Sync context**
- Add an `AGENT_CONTEXT.json` entry for the shell command surface upgrade.

**Step 4: Commit**
`git add AGENT_CONTEXT.json tests/test_cli.py archon/cli_commands.py archon/cli_interactive_commands.py archon/cli_repl_commands.py archon/cli.py && git commit -m "feat: upgrade terminal slash command surface"`
