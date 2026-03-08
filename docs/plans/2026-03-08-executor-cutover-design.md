# Executor Cutover Design (Parity-First)

## Decision

Do not replace the current `hybrid(route-only)` bridge with a new executor in-place.

Instead, run a parity-first cutover that:
- extracts the legacy turn-execution loop into one shared executor
- proves behavior parity against the current `Agent.run()` and `Agent.run_stream()` paths
- switches non-streaming first
- switches streaming only after parity is established

This keeps current product behavior stable while removing the last major live legacy seam intentionally, not cosmetically.

## Current Reality

Archon is cleaner than it was, but the orchestrator is still a route wrapper over legacy execution:
- `archon/control/orchestrator.py` classifies and emits hooks
- `hybrid` still calls legacy execution directly
- `archon/agent.py` still owns the real turn loop for:
  - history mutation
  - tool sequencing
  - policy checks
  - MCP policy checks
  - tool-result truncation/redaction
  - iteration budget and loop detection
  - stream/non-stream control flow

That means the current debt is architectural, not hidden behavior.

## Why A Careful Cutover Is Required

The remaining execution logic is not thin glue. It is the behavior-critical center of the product.

A rushed cutover would likely break:
1. tool result ordering and history repair
2. policy/hook sequencing
3. stream vs non-stream parity
4. loop detection and iteration budgets
5. subtle shell/Telegram UX assumptions that depend on current hook timing

The correct move is not “delete the bridge.” The correct move is “extract the executor, prove parity, then switch callers.”

## Goals

1. Remove the live route-to-legacy execution seam from `orchestrator.py`.
2. Keep user-visible behavior stable during migration.
3. Preserve one execution model for both terminal and Telegram.
4. Avoid duplicating the turn loop in two places.
5. Make future planner/router evolution possible without touching tool execution again.

## Non-Goals

1. Rewriting the tool registry.
2. Redesigning worker execution or MCP in the same project.
3. Replacing the classifier/router logic in this cutover.
4. Introducing new backends or isolation models here.

## Recommended Architecture

### 1. Extract A Shared Turn Executor

Create one explicit executor layer responsible for a single assistant turn.

Suggested shape:
- `archon/execution/turn_executor.py`
  - `execute_turn(...)`
  - `execute_turn_stream(...)`

Responsibilities:
- call the model
- append assistant tool-use messages
- run tools with policy/hook checks
- append tool results
- enforce tool-result truncation
- enforce iteration budget / loop detection
- return final response state

This should be extracted from the existing `Agent` logic, not reimagined.

### 2. Keep Agent As Session Owner

`Agent` should continue to own:
- history
- counters/tokens
- session state
- pending compactions
- log labels
- hook emission plumbing
- policy profile selection

`Agent` should stop owning the inner execution loop directly once the executor is extracted.

### 3. Make Orchestrator Call The Executor

`archon/control/orchestrator.py` should become a real control-plane boundary:
- classify lane
- emit route hooks
- choose the correct executor path
- fall back only at explicit migration boundaries

It should no longer “bridge into legacy” once cutover is complete.

### 4. Switch Non-Stream Before Stream

Non-streaming execution is the safer first cut because it has fewer moving pieces.

Order:
1. extract shared non-stream executor
2. make `Agent.run()` call it directly
3. make `orchestrator.orchestrate_response()` call the same executor path
4. prove parity
5. repeat for streaming

### 5. Keep Compatibility Flags Until The End

During migration:
- keep `legacy` mode available
- keep `hybrid(route-only)` available while parity is being proven
- add a temporary internal path marker for the extracted executor

Only remove the old bridge after parity tests are stable.

## Cutover Phases

### Phase 1: Extract Non-Stream Executor Internals
- move the tool-loop internals from `Agent.run()` into a shared executor module
- do not change user-visible behavior
- keep `Agent.run()` as a thin wrapper around that executor

### Phase 2: Route Orchestrator Non-Stream Through Shared Executor
- make orchestrator call the same non-stream executor path
- remove direct bridge behavior for non-stream hybrid execution
- keep legacy fallback behind config or explicit guard during transition

### Phase 3: Extract Streaming Executor Internals
- repeat the same extraction pattern for `Agent.run_stream()`
- preserve current streaming/tool-call semantics exactly

### Phase 4: Route Orchestrator Stream Through Shared Executor
- eliminate the streaming bridge path
- keep parity assertions and rollback switch until stable

### Phase 5: Remove Bridge-Only Labels And Dead Compatibility Code
- delete bridge-only helpers
- update docs/context to state that hybrid is now a real executor path

## Required Parity Guarantees

Before removing the bridge, prove parity for:
1. final text-only responses
2. tool-call iterations with multiple tools
3. policy-denied tools
4. MCP policy-denied calls
5. loop-detection early stop
6. iteration-limit behavior
7. stream final-text responses
8. stream tool-then-final responses

Parity matters more than elegance in this migration.

## Risk Controls

1. Keep migration behind the current mode system until parity is proven.
2. Add fixture-style tests that compare old and new execution traces.
3. Move one responsibility at a time; do not redesign logic while extracting it.
4. Do non-stream first.
5. Delete dead code only after the replacement path is verified.

## Success Criteria

1. `orchestrator.py` no longer routes hybrid execution through a route-only legacy bridge.
2. `Agent.run()` and `Agent.run_stream()` become thin wrappers over shared executor functions.
3. Tool/policy/hook behavior remains stable.
4. Terminal and Telegram behavior remain unchanged at the product surface.
5. Full suite stays green throughout the migration.

## Judgement

This cutover is worth doing, but not as opportunistic cleanup.

It should be treated as a dedicated migration project because:
- the current seam is honest and contained
- the legacy loop still carries behavior-critical semantics
- the wrong cutover would create regressions faster than it removes debt

So the right decision is:
- do the cutover
- do it deliberately
- do it with parity as the primary objective
