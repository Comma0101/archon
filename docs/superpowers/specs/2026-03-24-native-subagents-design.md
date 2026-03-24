# Native Subagent System Design

## Problem

Archon can currently delegate only through external worker CLIs (`codex`, `claude_code`, `opencode`). That is strong for sandboxed or long-running work, but it is a poor fit for lightweight bounded delegation:

- every delegation pays CLI startup/auth/config latency
- there is no cheap model tiering for exploratory work
- there is no fresh in-process context window for bounded research
- workers introduce durable session/runtime state even when the task is short

The goal of native subagents is not to replace workers. It is to add a lightweight foreground delegation path for small, bounded tasks.

## Goals

1. Add a `spawn_subagent` tool that runs a fresh in-process subagent loop and returns a condensed result to the parent turn
2. Support two subagent types:
   - `explore`: cheap, read-only, codebase-oriented
   - `general`: inherited-model, mutation-capable, but still bounded and foreground
3. Add reusable model tier config in `[llm.tiers]`
4. Preserve current Archon behavior where it matters:
   - LLM retry/timeout behavior
   - runtime tool-policy checks
   - tool-result history shaping
   - truthful `/cost` accounting
5. Coexist with the existing worker system instead of competing with it

## Non-Goals

- background or parallel native subagents
- persistent native subagent sessions or resume
- worker/session control from inside native subagents
- replacing `delegate_code_task`
- auto-routing by the orchestrator
- a broad new tool platform for subagents

## Design Principles

### 1. Fresh context, not a second product

A native subagent is a bounded child loop with fresh history. It is not a new assistant surface, not a background service, and not a persistent runtime.

### 2. Reuse Archon semantics where correctness matters

The main loop already solved several hard problems:

- provider retry/timeout behavior
- tool-policy denial behavior
- history-shaping for noisy tool results
- usage accounting

The native subagent design should reuse or extract those behaviors, not silently re-implement weaker versions.

### 3. Narrow the tool surface deliberately

`general` should not mean “everything the parent can do.” In the current codebase, “everything” includes worker/session tools that can create durable background state. Native subagents should stay bounded.

## High-Level Architecture

```text
Parent Agent
  │
  ├─ spawn_subagent(type="explore", task="find config parsing")
  │    ├─ resolve model tier -> light
  │    ├─ build fresh filtered registry
  │    ├─ run foreground subagent loop with fresh history
  │    ├─ apply normal retry/timeout + policy + history shaping
  │    └─ return condensed summary to parent as tool result
  │
  ├─ spawn_subagent(type="general", task="edit config loader")
  │    ├─ resolve model tier -> standard
  │    ├─ build fresh filtered registry
  │    ├─ run bounded foreground loop
  │    └─ return condensed summary
  │
  └─ delegate_code_task(...)
       external worker path remains the choice for sandboxed/heavy/durable work
```

The parent turn blocks while the native subagent runs. When the subagent completes, the parent receives a normal tool result string and continues.

## Model Tiers

### Config

```toml
[llm.tiers]
light = ""
standard = ""
```

- `light=""` means auto-detect from provider
- `standard=""` means inherit `[llm].model`

### Auto-Detection

| Provider | Light default |
|----------|---------------|
| Anthropic | `claude-haiku-4-5-20251001` |
| OpenAI | `gpt-4o-mini` |
| Google | `gemini-2.5-flash` |

### Tier Mapping

| Type | Tier |
|------|------|
| `explore` | `light` |
| `general` | `standard` |

This structure is intentionally reusable by other internal LLM callers later.

## Tool Surface

### Explore

Purpose: bounded read-only exploration of the local codebase.

Allowed tools:
- `read_file`
- `grep`
- `glob`
- `list_dir`
- `shell` with SAFE-only confirmation

Not allowed:
- all write/edit tools
- memory tools
- MCP tools
- setup tools
- worker/session tools
- call/mission tools
- `spawn_subagent`

### General

Purpose: bounded in-process task execution with local file mutation.

Allowed tools:
- local filesystem tools, including mutation
- content/web tools

Not allowed:
- worker/session tools (`delegate_code_task`, `worker_*`)
- call/mission tools
- `spawn_subagent`

Rationale: if a task needs durable background state, approvals across turns, or sandboxed external execution, the parent should call the worker system directly. A native subagent should not itself open a second delegation/runtime layer.

## Registry Strategy

Tool handlers in Archon are closures over the registry instance that registered them. That means copying parent handlers into a child registry is incorrect.

### Required approach

Build a fresh registry and re-register tools against that fresh instance.

### Required `ToolRegistry` support

Add a first-class empty constructor, for example:

```python
ToolRegistry.empty(
    *,
    archon_source_dir: str | None = None,
    confirmer: Callable[[str, Level], bool] | None = None,
    config: Config | None = None,
) -> ToolRegistry
```

This should initialize the same internal fields as `__init__`, but skip `_register_builtins()`.

Do not rely on raw `ToolRegistry.__new__` from feature code.

### Explore shell safety

`shell` already classifies commands before calling `registry.confirmer(...)`. The explore registry only needs a confirmer wrapper that rejects `Level.DANGEROUS` and `Level.FORBIDDEN`.

## Subagent Runner

### Core shape

`archon/subagents/runner.py`

The runner owns:
- fresh `history`
- dedicated `LLMClient`
- filtered `ToolRegistry`
- token counters
- iteration/time budgeting

It does **not** reuse `Agent.run()` wholesale, because that would also drag in parent-surface concerns like activity injection and preference capture. But it also should **not** be a weaker ad hoc loop.

### Required parity with main loop

The runner must reuse shared helpers for:

1. **LLM retry + timeout**
   - extract the current non-streaming retry/timeout helper used by the main agent
   - subagents must not do raw `llm.chat(...)` calls without that wrapper

2. **Tool-result shaping**
   - extract the current history-shaping logic used by the main agent for `shell`, `read_file`, sampled tools, and generic truncation
   - subagents must feed shaped results back into history, not raw full outputs

3. **Runtime policy checks**
   - before tool execution, call the same standalone policy evaluators used by the main executor
   - use `profile_name="default"` for runtime policy evaluation
   - registry filtering remains the primary allowlist; policy remains the second safety layer

### Result contract

```python
@dataclass
class SubagentResult:
    status: str           # ok | failed | timeout | iteration_limit
    text: str
    input_tokens: int
    output_tokens: int
    iterations_used: int
```

Unlike `execute_turn()`, the runner returns structured status directly.

### Suspension behavior

Native subagents do not support suspension. If a tool returns `SuspensionRequest`, the subagent should fail fast with a clear error result. This is another reason worker/session tools are excluded from the native surface.

## Policy Handling

Subagents do not get their own profile system in v1.

Instead:
- tool visibility is enforced by the registry that was built for the subagent type
- runtime policy evaluation uses the existing `default` profile
- explore safety is further tightened by its confirmer wrapper

That keeps v1 aligned with the current executor model without inventing a second policy stack.

## Token Accounting

Subagent usage must show up in both:
- parent in-memory token counters
- ledger-backed workflow totals used by `/cost`

### Required mechanism

After `spawn_subagent` completes, emit a structured usage event through the registry execute-event channel, for example:

```python
handler("subagent_usage", {
    "source": "subagent:explore",
    "provider": provider,
    "model": model,
    "input_tokens": ...,
    "output_tokens": ...,
})
```

The parent `Agent._on_tool_execute_event()` must handle that event by:
- incrementing `total_input_tokens` / `total_output_tokens`
- recording a normalized usage ledger event for the parent session

No direct ledger writing should happen from inside the subagent runner.

## `spawn_subagent` Tool

### Schema

```python
spawn_subagent(
    task: str,
    type: str = "explore",
    context: str = "",
) -> str
```

### Result format

```text
subagent_type: explore
status: ok
iterations: 3/8
tokens: 1200 in, 450 out

<final text truncated to max_result_chars>
```

### Guidance

System guidance should explicitly position native subagents like this:
- use `spawn_subagent(type="explore")` for bounded exploration/research
- use `spawn_subagent(type="general")` for bounded in-process task execution
- use `delegate_code_task` for heavy, sandboxed, or durable worker work

## File Structure

```text
archon/subagents/
    __init__.py
    types.py
    registry.py
    runner.py
    tools.py
```

Likely shared-helper extraction:

```text
archon/execution/llm_runtime.py
archon/execution/history_shaping.py
```

## Integration Points

| File | Change |
|------|--------|
| `archon/config.py` | add `TierConfig` and `resolve_tier_model()` |
| `archon/tools.py` | add `ToolRegistry.empty(...)`; register `spawn_subagent` |
| `archon/agent.py` | handle `subagent_usage` events for counters + ledger |
| `archon/subagents/*` | new native subagent package |
| extracted shared helper module(s) | move retry/timeout and history-shaping logic out of `agent.py` |

## Error Handling

| Scenario | Behavior |
|----------|----------|
| invalid subagent type | tool returns immediate error string |
| empty task | tool returns immediate error string |
| LLM failure after retries | `status=failed` |
| wall-clock timeout | `status=timeout` |
| iteration limit | `status=iteration_limit` |
| repeated tool errors | `status=failed` |
| suspension request | `status=failed` with explicit non-support message |

All failures are contained inside the tool result. The parent turn continues normally.

## Acceptance Criteria

1. `spawn_subagent(type="explore", ...)` runs a fresh light-model subagent with SAFE-only read tools
2. `spawn_subagent(type="general", ...)` runs a fresh standard-model subagent with bounded local execution tools
3. Native subagents cannot spawn native subagents
4. Native subagents cannot start worker/session flows
5. Subagent tool execution still honors runtime policy checks
6. Subagent history uses the same shaping/truncation strategy as the main loop
7. Subagent LLM calls use the same retry/timeout semantics as the main loop
8. `/cost` reflects subagent token usage in both chat-session and workflow totals
9. Existing worker tools continue to work unchanged from the parent surface
10. All existing tests continue to pass
