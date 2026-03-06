# Tool Registry Modularization (Phase 3: Test Split) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Split `tests/test_tools.py` into smaller domain-specific test modules without changing test behavior, assertions, or coverage.

**Architecture:** Keep the same test logic and monkeypatch targets; only reorganize by domain. Pytest discovery will collect the new files instead of the monolith.

**Tech Stack:** Pytest (existing suite)

---

## Scope

- Replace `tests/test_tools.py` with:
  - `tests/test_tools_registry_filesystem.py`
  - `tests/test_tools_memory.py`
  - `tests/test_tools_content.py`
  - `tests/test_tools_workers.py`
- Keep test code/behavior unchanged (mechanical move only)
- No production code changes in this phase

## Tasks

### Task 1: Baseline verification
- Run `python -m pytest tests/test_tools.py -q`
- Expected: PASS (current baseline)

### Task 2: Split file by class groups (mechanical extraction)
- `registry/filesystem`: `TestRegistry`, `TestReadFile`, `TestWriteFile`, `TestEditFile`, `TestListDir`, `TestShell`
- `memory`: `TestMemoryLookupTool`
- `content`: `TestNewsBriefTool`, `TestWebTools`
- `workers`: `TestDelegateCodeTask`
- Duplicate shared imports + `make_registry()` helper in each file (acceptable, low risk)
- Remove original `tests/test_tools.py`

### Task 3: Verification
- Run `python -m pytest tests/ -q`
- Expected: PASS (test count unchanged)

### Task 4: Context update
- Update `AGENT_CONTEXT.json` test module entries to reflect split files

---

## Out of Scope

1. Refactoring test helper imports/shared fixtures
2. Renaming/reorganizing test classes for style
3. Worker adapter helper deduplication
4. Telegram API client consolidation
