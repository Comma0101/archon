# Executor Cutover Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the route-only legacy execution bridge with a shared turn executor while preserving current Archon behavior.

**Architecture:** Extract the inner turn loop from `Agent.run()` and `Agent.run_stream()` into shared execution functions, then switch orchestrator callers to those shared functions phase by phase. Keep parity tests and rollback-safe migration boundaries until the bridge is fully removed.

**Tech Stack:** Python 3.11+, existing Archon control/execution modules, pytest.

---

### Task 1: Add Shared Execution Fixture Tests For Non-Stream Behavior

**Files:**
- Modify: `tests/test_agent.py`
- Modify: `tests/test_cli.py`

**Step 1: Write the failing test**

Add tests that capture current non-stream behavior for:
- plain text response
- one tool call
- denied tool call
- MCP deny path
- loop-detection stop

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_agent.py -q -k "executor parity or loop detection or policy deny"`
Expected: FAIL because shared executor parity harness does not exist yet.

**Step 3: Write minimal implementation**

Add fixture helpers or test utilities that exercise current `Agent.run()` behavior as the baseline.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_agent.py -q -k "executor parity or loop detection or policy deny"`
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_agent.py tests/test_cli.py
git commit -m "test: capture non-stream executor parity fixtures"
```

### Task 2: Extract Shared Non-Stream Turn Executor

**Files:**
- Create: `archon/execution/turn_executor.py`
- Modify: `archon/agent.py`
- Test: `tests/test_agent.py`

**Step 1: Write the failing test**

Add tests that call the new non-stream executor directly and expect the same outputs/history mutations as the current `Agent.run()` path.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_agent.py -q -k "turn executor and non-stream"`
Expected: FAIL because the shared executor module does not exist.

**Step 3: Write minimal implementation**

- Create `execute_turn(...)` in `archon/execution/turn_executor.py`.
- Move only the non-stream inner execution loop from `Agent.run()`.
- Keep `Agent.run()` as a thin wrapper.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_agent.py -q -k "turn executor and non-stream"`
Expected: PASS.

**Step 5: Commit**

```bash
git add archon/execution/turn_executor.py archon/agent.py tests/test_agent.py
git commit -m "refactor: extract shared non-stream turn executor"
```

### Task 3: Route Orchestrator Non-Stream Through Shared Executor

**Files:**
- Modify: `archon/control/orchestrator.py`
- Modify: `archon/agent.py`
- Test: `tests/test_agent.py`

**Step 1: Write the failing test**

Add tests asserting hybrid non-stream execution no longer uses a route-only legacy bridge path marker and instead uses the shared executor path.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_agent.py -q -k "hybrid and executor path"`
Expected: FAIL because hybrid still bridges into legacy execution.

**Step 3: Write minimal implementation**

- Change non-stream orchestrator execution to call the shared executor path.
- Preserve existing hooks and fallback behavior during transition.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_agent.py -q -k "hybrid and executor path"`
Expected: PASS.

**Step 5: Commit**

```bash
git add archon/control/orchestrator.py archon/agent.py tests/test_agent.py
git commit -m "refactor: route hybrid non-stream execution through shared executor"
```

### Task 4: Add Shared Streaming Parity Tests

**Files:**
- Modify: `tests/test_agent.py`

**Step 1: Write the failing test**

Add tests for streaming parity:
- text-only stream
- tool-then-final stream
- denied tool in stream path

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_agent.py -q -k "stream parity or run_stream"`
Expected: FAIL because the shared streaming executor path does not exist.

**Step 3: Write minimal implementation**

Add fixture-level parity assertions for current `run_stream()` behavior.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_agent.py -q -k "stream parity or run_stream"`
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_agent.py
git commit -m "test: capture streaming executor parity fixtures"
```

### Task 5: Extract Shared Streaming Turn Executor

**Files:**
- Modify: `archon/execution/turn_executor.py`
- Modify: `archon/agent.py`
- Test: `tests/test_agent.py`

**Step 1: Write the failing test**

Add tests that call the shared streaming executor directly and expect the same emitted output/history effects as `Agent.run_stream()`.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_agent.py -q -k "turn executor and stream"`
Expected: FAIL because the streaming executor path is not shared yet.

**Step 3: Write minimal implementation**

- Add `execute_turn_stream(...)` to `archon/execution/turn_executor.py`.
- Move the streaming inner loop out of `Agent.run_stream()`.
- Keep `Agent.run_stream()` as a thin wrapper.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_agent.py -q -k "turn executor and stream"`
Expected: PASS.

**Step 5: Commit**

```bash
git add archon/execution/turn_executor.py archon/agent.py tests/test_agent.py
git commit -m "refactor: extract shared streaming turn executor"
```

### Task 6: Route Orchestrator Streaming Through Shared Executor

**Files:**
- Modify: `archon/control/orchestrator.py`
- Modify: `archon/agent.py`
- Test: `tests/test_agent.py`

**Step 1: Write the failing test**

Add tests asserting hybrid streaming execution no longer uses the route-only legacy bridge and instead routes through the shared streaming executor.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_agent.py -q -k "hybrid and stream executor"`
Expected: FAIL because hybrid stream still bridges into legacy execution.

**Step 3: Write minimal implementation**

- Change streaming orchestrator execution to call the shared streaming executor path.
- Preserve fallback and hook behavior.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_agent.py -q -k "hybrid and stream executor"`
Expected: PASS.

**Step 5: Commit**

```bash
git add archon/control/orchestrator.py archon/agent.py tests/test_agent.py
git commit -m "refactor: route hybrid stream execution through shared executor"
```

### Task 7: Remove Route-Only Bridge Labels And Dead Helpers

**Files:**
- Modify: `archon/control/orchestrator.py`
- Modify: `AGENT_CONTEXT.json`
- Test: `tests/test_agent.py`

**Step 1: Write the failing test**

Add tests asserting route payloads no longer advertise `hybrid_legacy_bridge_v0` markers once the shared executor cutover is complete.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_agent.py -q -k "bridge marker"`
Expected: FAIL because legacy bridge labels still exist.

**Step 3: Write minimal implementation**

- Remove the bridge-only path markers and dead compatibility helpers.
- Update context/status wording to describe hybrid truthfully after cutover.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_agent.py -q -k "bridge marker"`
Expected: PASS.

**Step 5: Commit**

```bash
git add archon/control/orchestrator.py AGENT_CONTEXT.json tests/test_agent.py
git commit -m "refactor: remove legacy bridge markers after executor cutover"
```

### Task 8: Full Parity Verification And Rollout Check

**Files:**
- Modify: `AGENT_CONTEXT.json`
- Test: `tests/test_agent.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_telegram_adapter.py`
- Test: `tests/test_tools_content.py`
- Test: `tests/test_tools_workers.py`

**Step 1: Run focused suites**

Run:
```bash
pytest tests/test_agent.py tests/test_cli.py tests/test_telegram_adapter.py -q
pytest tests/test_tools_content.py tests/test_tools_workers.py -q
```
Expected: PASS.

**Step 2: Run full suite**

Run:
```bash
XDG_STATE_HOME=/tmp/archon-state python -m pytest tests -q
```
Expected: PASS.

**Step 3: Sync context**

Document the executor cutover result and remaining non-goals in `AGENT_CONTEXT.json`.

**Step 4: Commit**

```bash
git add AGENT_CONTEXT.json
git commit -m "docs: sync executor cutover migration context"
```
