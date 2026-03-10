# Accept Reads And Search Tools Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `accept_reads` a real permission mode and add first-class read-only `glob` and `grep` tools.

**Architecture:** Keep the change inside the existing filesystem tool registration layer. Add small permission helpers and two new read-only tool handlers in `archon/tooling/filesystem_tools.py`, then cover the behavior with focused tests before running the full suite.

**Tech Stack:** Python, pytest, existing `ToolRegistry` filesystem tool registration.

---

### Task 1: Add failing permission-mode tests

**Files:**
- Modify: `tests/test_filesystem_tools_confirm.py`

**Step 1: Write the failing tests**
- Add tests proving `accept_reads` allows `read_file` and `list_dir` without confirmation.
- Add a control test proving `confirm_all` still confirms the same operations.

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_filesystem_tools_confirm.py -q -k 'accept_reads or confirm_all'`
Expected: FAIL because current read tools do not distinguish the modes.

**Step 3: Write minimal implementation**
- Add a small helper in `archon/tooling/filesystem_tools.py` for read-only tool confirmation semantics.
- Apply it to `read_file` and `list_dir` only.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_filesystem_tools_confirm.py -q -k 'accept_reads or confirm_all'`
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_filesystem_tools_confirm.py archon/tooling/filesystem_tools.py
git commit -m "fix: implement accept_reads for filesystem reads"
```

### Task 2: Add failing `glob` tool tests

**Files:**
- Modify: `tests/test_tools_registry_filesystem.py`

**Step 1: Write the failing tests**
- Add a schema-name/count expectation for `glob`.
- Add a behavior test that finds matching files under a temporary root.

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tools_registry_filesystem.py -q -k 'glob'`
Expected: FAIL because the tool is not registered.

**Step 3: Write minimal implementation**
- Register `glob` in `archon/tooling/filesystem_tools.py`.
- Return bounded absolute paths for matching files.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tools_registry_filesystem.py -q -k 'glob'`
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_tools_registry_filesystem.py archon/tooling/filesystem_tools.py
git commit -m "feat: add glob filesystem tool"
```

### Task 3: Add failing `grep` tool tests

**Files:**
- Modify: `tests/test_tools_registry_filesystem.py`

**Step 1: Write the failing tests**
- Add a schema-name/count expectation for `grep`.
- Add a behavior test that finds matching lines under a temporary root.
- Add a filename-filter test using an optional glob filter.

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tools_registry_filesystem.py -q -k 'grep'`
Expected: FAIL because the tool is not registered.

**Step 3: Write minimal implementation**
- Register `grep` in `archon/tooling/filesystem_tools.py`.
- Return bounded `path:line:text` matches.
- Skip unreadable files best-effort.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tools_registry_filesystem.py -q -k 'grep'`
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_tools_registry_filesystem.py archon/tooling/filesystem_tools.py
git commit -m "feat: add grep filesystem tool"
```

### Task 4: Verify combined behavior

**Files:**
- Modify if needed: `archon/tooling/filesystem_tools.py`
- Verify: `tests/test_filesystem_tools_confirm.py`
- Verify: `tests/test_tools_registry_filesystem.py`

**Step 1: Run focused slice**

Run: `python -m pytest tests/test_filesystem_tools_confirm.py tests/test_tools_registry_filesystem.py -q`
Expected: PASS.

**Step 2: Run full suite**

Run: `XDG_STATE_HOME=/tmp/archon-state python -m pytest tests -q`
Expected: PASS.

**Step 3: Sync context**
- Update `AGENT_CONTEXT.json` with the new permission semantics and tool additions.

**Step 4: Commit**

```bash
git add AGENT_CONTEXT.json archon/tooling/filesystem_tools.py tests/test_filesystem_tools_confirm.py tests/test_tools_registry_filesystem.py
git commit -m "feat: add read-only search tools and accept_reads support"
```
