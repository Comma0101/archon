# Tool Registry Modularization (Phase 1) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reduce `archon/tools.py` size and maintenance risk by extracting non-worker tool registrations into helper modules with zero behavior changes.

**Architecture:** Keep `archon/tools.py` as the public module (`ToolRegistry` stays in place) to avoid import churn and a module/package collision. Introduce `archon/tooling/*` registration helpers and shared local tool utilities, then have `ToolRegistry._register_builtins()` delegate to them before worker tool registration.

**Tech Stack:** Python stdlib, existing `pytest` suite (no new runtime dependencies)

---

## Design (Approved Scope Assumption)

- **No behavior changes**
  - Same tool names, schemas, outputs, and confirmation behavior
  - Same imports for callers (`from archon.tools import ToolRegistry`)
- **No `archon/tools/` package yet**
  - Avoids collision with existing `archon/tools.py`
- **Phase 1 only**
  - Extract non-worker registrations:
    - filesystem/basic tools (`shell`, `read_file`, `write_file`, `edit_file`, `list_dir`)
    - memory tools (`memory_*`)
    - news/web tools (`news_brief`, `web_search`, `web_read`)
  - Leave worker tools in `archon/tools.py` for a later pass

## Files

### Create
- `archon/tooling/__init__.py`
- `archon/tooling/common.py`
- `archon/tooling/filesystem_tools.py`
- `archon/tooling/memory_tools.py`
- `archon/tooling/content_tools.py`

### Modify
- `archon/tools.py`

### Test
- `tests/test_tools.py`

---

### Task 1: Add shared local tool helpers

**Files:**
- Create: `archon/tooling/common.py`
- Create: `archon/tooling/__init__.py`
- Modify: `archon/tools.py`
- Test: `tests/test_tools.py`

**Step 1: Write the failing test**

No new behavior is introduced. Existing `tests/test_tools.py` acts as regression coverage.

**Step 2: Run test to verify current baseline**

Run: `HOME=/tmp PYTHONPATH=/home/comma/.local/lib/python3.14/site-packages python -m pytest tests/test_tools.py -q`
Expected: PASS (baseline snapshot before mechanical refactor)

**Step 3: Write minimal implementation**

- Add `archon/tooling/common.py` with:
  - `truncate_text(text, max_chars)`
  - `auto_commit(source_dir)` (moved from `archon/tools.py`)
- Update `archon/tools.py` to import `truncate_text` (optionally alias as `_truncate`) and remove local `_auto_commit`

**Step 4: Run tests to verify**

Run: `HOME=/tmp PYTHONPATH=/home/comma/.local/lib/python3.14/site-packages python -m pytest tests/test_tools.py -q`
Expected: PASS

---

### Task 2: Extract filesystem/basic tool registrations

**Files:**
- Create: `archon/tooling/filesystem_tools.py`
- Modify: `archon/tools.py`
- Test: `tests/test_tools.py`

**Step 1: Write/identify failing test**

Use existing regression tests:
- `TestReadFile`
- `TestWriteFile`
- `TestEditFile`
- `TestListDir`
- `TestShell`

**Step 2: Run focused tests (baseline)**

Run: `HOME=/tmp PYTHONPATH=/home/comma/.local/lib/python3.14/site-packages python -m pytest tests/test_tools.py -q -k 'ReadFile or WriteFile or EditFile or ListDir or Shell'`
Expected: PASS

**Step 3: Write minimal implementation**

- Add `register_filesystem_tools(registry)` in `archon/tooling/filesystem_tools.py`
- Move closures + registrations for:
  - `shell`
  - `read_file`
  - `write_file`
  - `edit_file`
  - `list_dir`
- Keep outputs/strings identical
- In `ToolRegistry._register_builtins()`, call helper and remove moved blocks

**Step 4: Run focused tests**

Run: same focused pytest command
Expected: PASS

---

### Task 3: Extract memory tool registrations

**Files:**
- Create: `archon/tooling/memory_tools.py`
- Modify: `archon/tools.py`
- Test: `tests/test_tools.py`

**Step 1: Regression tests**

Use existing `TestMemoryLookupTool`.

**Step 2: Run focused tests (baseline)**

Run: `HOME=/tmp PYTHONPATH=/home/comma/.local/lib/python3.14/site-packages python -m pytest tests/test_tools.py -q -k 'MemoryLookupTool'`
Expected: PASS

**Step 3: Write minimal implementation**

- Add `register_memory_tools(registry)` and move:
  - `memory_read`
  - `memory_write`
  - `memory_lookup`
  - `memory_inbox_add`
  - `memory_inbox_list`
  - `memory_inbox_decide`
- Preserve canonicalization/inbox formatting behavior exactly

**Step 4: Run focused tests**

Run: same focused pytest command
Expected: PASS

---

### Task 4: Extract news/web tool registrations

**Files:**
- Create: `archon/tooling/content_tools.py`
- Modify: `archon/tools.py`
- Test: `tests/test_tools.py`

**Step 1: Regression tests**

Use existing:
- `TestNewsBriefTool`
- `TestWebTools`

**Step 2: Run focused tests (baseline)**

Run: `HOME=/tmp PYTHONPATH=/home/comma/.local/lib/python3.14/site-packages python -m pytest tests/test_tools.py -q -k 'NewsBriefTool or WebTools'`
Expected: PASS

**Step 3: Write minimal implementation**

- Add `register_content_tools(registry)` and move:
  - `news_brief`
  - `web_search`
  - `web_read`
- Use shared `truncate_text` for snippets

**Step 4: Run focused tests**

Run: same focused pytest command
Expected: PASS

---

### Task 5: Wire and verify full tool registry behavior

**Files:**
- Modify: `archon/tools.py`
- Test: `tests/test_tools.py`

**Step 1: Ensure registration order and count unchanged**

- Keep tool count at 23
- Preserve schemas and names

**Step 2: Run full tool tests**

Run: `HOME=/tmp PYTHONPATH=/home/comma/.local/lib/python3.14/site-packages python -m pytest tests/test_tools.py -q`
Expected: PASS

**Step 3: Run full suite**

Run: `HOME=/tmp PYTHONPATH=/home/comma/.local/lib/python3.14/site-packages python -m pytest tests/ -q`
Expected: PASS (currently 167)

---

## Follow-up Phases (Not in this patch)

1. **Phase 2:** Extract worker tool registrations from `archon/tools.py` into `archon/tooling/worker_tools.py`
2. **Phase 3:** Split `tests/test_tools.py` by domain (`test_tools_filesystem.py`, `test_tools_memory.py`, `test_tools_workers.py`, etc.)
3. **Phase 4:** Consolidate duplicate Telegram Bot API client logic used by adapter and news runner
4. **Phase 5:** Deduplicate worker adapter helpers (`_truncate`, `_first_nonempty_line`, summary helpers) via shared worker utility module
