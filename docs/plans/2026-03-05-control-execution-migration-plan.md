# Control/Execution Migration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Split Archon into explicit control and execution planes through a compatibility-first migration with no default behavior change.

**Architecture:** Introduce new control/execution packages, shared contracts, and feature-flagged orchestration. Keep current `Agent` loop, tools, and worker adapters as the source of truth during parity stages. Move logic by extraction + bridge wrappers, not rewrite.

**Tech Stack:** Python 3.11+, existing Archon modules, pytest.

---

### Task 1: Add Contracts and Orchestrator Config Flags (No-op Path)

**Files:**
- Create: `archon/control/contracts.py`
- Create: `archon/execution/contracts.py`
- Modify: `archon/config.py`
- Test: `tests/test_config.py`

**Step 1: Write failing config test**

Add tests proving new `orchestrator` defaults load as:
- `enabled == False`
- `mode == "legacy"`
- `shadow_eval == True`

**Step 2: Run test to verify RED**

Run: `pytest tests/test_config.py -q`  
Expected: failure due missing config fields.

**Step 3: Write minimal implementation**

- Add `OrchestratorConfig` dataclass and attach to `Config`.
- Parse `[orchestrator]` section from TOML with safe defaults.
- Add lightweight contract dataclasses only (no behavior wiring yet).

**Step 4: Run tests to verify GREEN**

Run: `pytest tests/test_config.py -q`  
Expected: pass.

**Step 5: Commit**

```bash
git add archon/config.py archon/control/contracts.py archon/execution/contracts.py tests/test_config.py
git commit -m "feat: add orchestrator config and shared migration contracts"
```

### Task 2: Introduce Hook Bus (No-op Handlers)

**Files:**
- Create: `archon/control/hooks.py`
- Modify: `archon/agent.py`
- Modify: `archon/tools.py`
- Test: `tests/test_agent.py`

**Step 1: Write failing hook lifecycle tests**

Add tests that assert lifecycle events are emitted in order for:
- tool call success
- tool call exception

**Step 2: Run tests to verify RED**

Run: `pytest tests/test_agent.py -q -k "hook"`  
Expected: no hook events emitted.

**Step 3: Implement minimal hook bus**

- Add `HookBus` with register/emit methods.
- Emit no-op-safe events from agent/tool execution boundaries.
- Do not alter returned tool content or agent response flow.

**Step 4: Run tests to verify GREEN**

Run: `pytest tests/test_agent.py -q -k "hook"`  
Expected: pass.

**Step 5: Commit**

```bash
git add archon/control/hooks.py archon/agent.py archon/tools.py tests/test_agent.py
git commit -m "feat: add control-plane hook bus with no-op lifecycle emission"
```

### Task 3: Add Policy Engine in Shadow Mode

**Files:**
- Create: `archon/control/policy.py`
- Modify: `archon/config.py`
- Modify: `archon/agent.py`
- Test: `tests/test_agent.py`
- Test: `tests/test_config.py`

**Step 1: Write failing policy tests**

Add tests for:
- default profile allows all (shadow mode logs decision only)
- denied tool in profile logs would-block decision but does not block in legacy mode

**Step 2: Run tests to verify RED**

Run: `pytest tests/test_agent.py tests/test_config.py -q -k "policy or profile"`  
Expected: missing policy/profile logic.

**Step 3: Implement minimal policy**

- Add profile schema parsing (`[profiles.<name>]`).
- Add policy evaluator that returns `allow | deny | shadow_deny`.
- In `legacy` mode: never enforce deny, only log/emit event.

**Step 4: Run tests to verify GREEN**

Run: `pytest tests/test_agent.py tests/test_config.py -q -k "policy or profile"`  
Expected: pass.

**Step 5: Commit**

```bash
git add archon/control/policy.py archon/config.py archon/agent.py tests/test_agent.py tests/test_config.py
git commit -m "feat: add capability policy engine in shadow mode"
```

### Task 4: Build Execution Bridge over Existing Worker Router

**Files:**
- Create: `archon/execution/worker_bridge.py`
- Create: `archon/execution/runner.py`
- Modify: `archon/workers/router.py`
- Modify: `archon/tooling/worker_delegate_tools.py`
- Test: `tests/test_worker_tools.py`

**Step 1: Write failing parity tests**

Add tests asserting legacy worker path and bridge path produce equivalent:
- selected worker
- status
- summary/error fields

**Step 2: Run tests to verify RED**

Run: `pytest tests/test_worker_tools.py -q -k "bridge or parity"`  
Expected: bridge missing.

**Step 3: Implement minimal bridge**

- Keep `archon/workers/router.py` as implementation backend.
- Wrap calls through `execution/runner.py` in compatibility mode.
- Do not change external tool responses.

**Step 4: Run tests to verify GREEN**

Run: `pytest tests/test_worker_tools.py -q -k "bridge or parity"`  
Expected: pass.

**Step 5: Commit**

```bash
git add archon/execution/worker_bridge.py archon/execution/runner.py archon/workers/router.py archon/tooling/worker_delegate_tools.py tests/test_worker_tools.py
git commit -m "refactor: add execution bridge over existing worker router"
```

### Task 5: Add Orchestrator Wrapper with Legacy/Hybrid Modes

**Files:**
- Create: `archon/control/orchestrator.py`
- Modify: `archon/agent.py`
- Modify: `archon/cli_runtime.py`
- Test: `tests/test_agent.py`

**Step 1: Write failing routing-mode tests**

Add tests for:
- default legacy mode path unchanged
- hybrid mode calls orchestrator planning wrapper
- fallback to legacy on orchestrator failure

**Step 2: Run tests to verify RED**

Run: `pytest tests/test_agent.py -q -k "orchestrator or hybrid"`  
Expected: missing orchestrator wrapper.

**Step 3: Implement minimal orchestrator wrapper**

- Add `orchestrate_turn(...)` as a thin planner/router facade.
- In legacy mode: direct passthrough.
- In hybrid mode: evaluate route then execute via existing path.
- On internal error: safe fallback to legacy path + event log.

**Step 4: Run tests to verify GREEN**

Run: `pytest tests/test_agent.py -q -k "orchestrator or hybrid"`  
Expected: pass.

**Step 5: Commit**

```bash
git add archon/control/orchestrator.py archon/agent.py archon/cli_runtime.py tests/test_agent.py
git commit -m "feat: add feature-flagged orchestrator wrapper with safe legacy fallback"
```

### Task 6: End-to-End Regression, Docs, and Context Sync

**Files:**
- Modify: `AGENT_CONTEXT.json`
- Modify: `docs/plans/2026-03-05-control-execution-migration-design.md`

**Step 1: Run targeted regression suite**

Run:
```bash
pytest tests/test_agent.py tests/test_cli.py tests/test_worker_tools.py tests/test_config.py tests/test_web_search.py -q
```
Expected: all pass.

**Step 2: Run full core suite**

Run:
```bash
pytest tests/ -q
```
Expected: pass (or document environment-specific failures clearly).

**Step 3: Update context + design status**

- Append changelog entries per completed task.
- Mark current migration phase and default mode (`legacy`).

**Step 4: Commit**

```bash
git add AGENT_CONTEXT.json docs/plans/2026-03-05-control-execution-migration-design.md
git commit -m "docs: sync migration status and verification evidence"
```
