# Incremental Stability + Maintainability (Phase 1) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Improve reliability and maintainability without behavioral regressions by hardening terminal input UX and removing brittle home-directory test assumptions.

**Architecture:** Keep public CLI/tool behavior unchanged. Land small, test-first slices: first terminal prompt correctness for long readline input, then path-isolated filesystem/memory tests and small memory-tool cleanup to avoid stale import-time path coupling.

**Tech Stack:** Python 3.11+, pytest, Click/readline, existing Archon tool registry and memory modules.

---

### Task 1: Fix Long CLI Input Cursor/Overwrite Bug (readline-safe ANSI prompt)

**Files:**
- Modify: `archon/cli.py`
- Modify: `tests/test_cli.py`

**Step 1: Add failing regression test**

Add a test asserting prompt ANSI sequences are wrapped in readline non-printing markers (`\x01` / `\x02`).

**Step 2: Run test to verify RED**

Run: `pytest tests/test_cli.py -q`  
Expected: import/test failure due missing helper or prompt mismatch.

**Step 3: Implement minimal fix**

- Add helper to build readline-safe colored prompts.
- Use helper in interactive `input(...)` calls for chat and multiline paste modes.

**Step 4: Run test to verify GREEN**

Run: `pytest tests/test_cli.py -q`  
Expected: all CLI tests pass.

**Step 5: Commit**

```bash
git add archon/cli.py tests/test_cli.py
git commit -m "fix: make CLI prompts readline-safe for long input"
```

### Task 2: Remove Home-Directory Coupling in Filesystem/Memory Tests

**Files:**
- Modify: `tests/test_tools_registry_filesystem.py`
- Modify: `tests/test_tools_memory.py`

**Step 1: Reproduce failing filesystem tests (RED)**

Run:
```bash
pytest tests/test_tools_registry_filesystem.py::TestWriteFile::test_write_new_file -q -vv
```
Expected: fail with read-only filesystem error under `Path.home()/.cache/...`.

**Step 2: Implement minimal path-isolated test changes**

- Switch write/edit tests from `Path.home()/.cache/...` to pytest-managed writable temp paths (`tmp_path`).
- Keep assertions behavior-identical (write/edit semantics unchanged).

**Step 3: Run focused tests**

Run:
```bash
pytest tests/test_tools_registry_filesystem.py tests/test_tools_memory.py -q
```
Expected: focused suites pass in restricted environments.

**Step 4: Commit**

```bash
git add tests/test_tools_registry_filesystem.py tests/test_tools_memory.py
git commit -m "test: use tmp paths for filesystem and memory tool tests"
```

### Task 3: Reduce Memory Tool Path Staleness (import-time constant cleanup)

**Files:**
- Modify: `archon/tooling/memory_tools.py`
- Modify: `tests/test_tools_memory.py`

**Step 1: Add failing regression test (RED)**

Add test proving `memory_read` list mode reflects patched `archon.memory.MEMORY_DIR` without also patching `archon.tooling.memory_tools.MEMORY_DIR`.

**Step 2: Run targeted test to confirm RED**

Run:
```bash
pytest tests/test_tools_memory.py::TestMemoryLookupTool::test_memory_read_list_uses_runtime_memory_dir -q -vv
```
Expected: failure due stale imported `MEMORY_DIR` in memory tool module.

**Step 3: Implement minimal fix**

- Replace `from archon.config import MEMORY_DIR` usage with runtime checks against `memory_store.MEMORY_DIR`.
- Avoid behavior changes to tool text/output format.

**Step 4: Run targeted + focused tests**

Run:
```bash
pytest tests/test_tools_memory.py -q
pytest tests/test_tools_registry_filesystem.py -q
```
Expected: all pass.

**Step 5: Commit**

```bash
git add archon/tooling/memory_tools.py tests/test_tools_memory.py
git commit -m "fix: make memory tool directory checks runtime-safe"
```

### Task 4: Context Sync and Verification

**Files:**
- Modify: `AGENT_CONTEXT.json`

**Step 1: Update context metadata**

- Set `last_updated` to current work date.
- Add changelog entries for Task 1-3.
- Update module summary for `archon/cli.py` and `archon/tooling/memory_tools.py` if behavior details changed.

**Step 2: Run verification commands**

Run:
```bash
pytest tests/test_cli.py tests/test_tools_registry_filesystem.py tests/test_tools_memory.py -q
```

Optional broader check (environment permitting):
```bash
pytest tests/ -q
```

**Step 3: Final commit**

```bash
git add AGENT_CONTEXT.json
git commit -m "docs: sync agent context for stability and test-hardening changes"
```
