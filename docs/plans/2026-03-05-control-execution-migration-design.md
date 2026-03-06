# Control/Execution Split Migration Design (Compatibility-First)

## Decision

Adopt a compatibility-first migration that separates Archon into:
- **Control plane**: planning, routing, policy, hooks, session intelligence
- **Execution plane**: worker/process execution, tool runtime, sandbox adapters

This keeps current behavior stable while creating a clean foundation for multi-agent growth.

## Why This Shape

Current Archon already has strong building blocks:
- Stable single-agent loop and history repair in `archon/agent.py`
- Persistent delegated worker sessions in `archon/tooling/worker_*` + `archon/workers/*`
- Basic safety gating in `archon/safety.py`

What is missing is not capability volume, but architecture boundaries:
- No first-class orchestrator abstraction
- No global hook bus for policy/automation around every tool event
- No declarative per-agent capability profile model
- No explicit execution-backend boundary (host vs sandbox/container)

## Goals

1. Preserve all current user-facing behavior by default.
2. Introduce explicit control/execution contracts.
3. Enable policy-driven multi-agent routing incrementally.
4. Prepare for optional hardened execution isolation without rewriting the agent loop.

## Non-Goals

1. Big-bang rewrite of `Agent.run()` and tool registry.
2. Immediate replacement of existing worker adapters (`codex`, `claude_code`, `opencode`).
3. Mandatory containerization from day one.

## Architecture

### 1) Control Plane (new package: `archon/control/`)

- `contracts.py`
  - Shared data contracts:
    - `TaskSpec`
    - `ExecutionRequest`
    - `ExecutionResult`
    - `HookEvent`
    - `CapabilityProfile`
- `orchestrator.py`
  - Planner/router entrypoint.
  - Modes:
    - `legacy` (default): no routing change
    - `hybrid`: routing decisions with legacy fallback
- `policy.py`
  - Capability checks before execution.
  - Applies profile rules (allowed tools, mode limits, network/fs policy hints).
- `hooks.py`
  - Lifecycle hook bus:
    - `pre_tool`
    - `post_tool`
    - `pre_delegate`
    - `post_delegate`
    - `session_end`
- `session_controller.py`
  - Sticky worker/session affinity logic extracted from tool modules over time.

### 2) Execution Plane (new package: `archon/execution/`)

- `contracts.py`
  - Execution-only runtime types (process status, runtime metrics).
- `runner.py`
  - Uniform execution entrypoint:
    - `run_task(request: ExecutionRequest) -> ExecutionResult`
- `tool_executor.py`
  - Wraps existing `ToolRegistry.execute` behavior through control hooks/policy.
- `sandbox.py`
  - Backend abstraction:
    - `host` (initial default)
    - `subprocess-restricted` (future)
    - `container` (future)
- `worker_bridge.py`
  - Compatibility adapter over existing `archon/workers/router.py` and adapters.

## Compatibility Invariants

The following must not change during migration phases 0-2:

1. Existing CLI commands and outputs continue to work.
2. Existing tool names and schemas remain stable.
3. Existing worker session IDs and persisted session files remain valid.
4. `delegate_code_task` and `worker_*` tools keep the same contract.
5. Default config path uses current behavior unless explicitly enabled.

## Configuration Additions

In `archon/config.py`:
- `[orchestrator]`
  - `enabled = false`
  - `mode = "legacy"` (`legacy | hybrid`)
  - `shadow_eval = true` (log decisions without enforcing initially)
- `[profiles.default]`
  - `allowed_tools = ["*"]`
  - `max_mode = "implement"`
  - `execution_backend = "host"`

No existing config keys are removed.

## Migration Phases

### Phase 0: Contracts + Flags (No Behavior Change)
- Add control/execution contract dataclasses.
- Add orchestrator config with safe defaults.
- Wire no-op orchestrator path.

### Phase 1: Hook Bus + Policy Shadow Mode
- Instrument tool and delegate lifecycle events.
- Run policy in log-only mode.
- Record policy decisions without blocking.

### Phase 2: Execution Plane Bridge
- Route delegated execution through `execution/worker_bridge.py`.
- Keep old worker adapters untouched.
- Add parity tests for same inputs/outputs.

### Phase 3: Enforced Profiles (Opt-In)
- Enforce selected profile rules in hybrid mode.
- Keep rollback switch to legacy path.

### Phase 4: Optional Isolation Backends
- Introduce subprocess/container backends behind config.
- Gradual rollout per profile.

## Current Status (2026-03-05)

- Default runtime mode is still `legacy` with behavior-preserving fallback.
- Completed:
  - Phase 0 foundations: control/execution contracts and `[orchestrator]` config flags.
  - Phase 1 foundations: hook bus emission and profile-aware policy evaluation with shadow/non-blocking default behavior.
  - Phase 2 bridge: delegated execution now enters through execution-plane runner/bridge while preserving legacy worker router behavior and tool contracts.
  - Phase 3 groundwork: session + per-turn policy profile selection added in `Agent` and live REPL controls exposed via `/profile [show|set <name>]`.
- Remaining focus:
  - Phase 3 enforcement rollout (opt-in): enforce profile denies in hybrid mode where configured.
  - Phase 4 backends: implement and harden optional non-host execution backends.

## Risk Controls

1. Feature flags for every behavioral boundary.
2. Shadow mode before enforcement.
3. Contract tests comparing legacy vs migrated paths.
4. Fast rollback: one config switch back to `legacy`.
5. Changelog/context updates at each phase.

## Success Criteria

1. Default install remains behavior-identical.
2. Hybrid mode can route selected tasks without regressions.
3. Policy decisions are explainable and logged.
4. Execution backend can be swapped by profile without changing control logic.
