# Native Subagents Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a bounded in-process `spawn_subagent` tool with model tiering, fresh context windows, truthful token accounting, and parity with Archon’s current retry/policy/history semantics.

**Architecture:** Native subagents run through a purpose-built foreground runner, but that runner must reuse shared helpers for non-streaming LLM retry/timeout and tool-result history shaping. Tool access comes from freshly built registries, not copied parent handlers, and runtime policy checks remain active using the default profile.

**Tech Stack:** Python, existing `LLMClient`, `ToolRegistry`, policy evaluators, usage ledger, pytest

**Spec:** `docs/superpowers/specs/2026-03-24-native-subagents-design.md`

---

### Task 1: Add tier config and tier resolution

**Files:**
- Modify: `archon/config.py`
- Create: `tests/test_subagent_tier_config.py`

**Step 1: Write failing tests for `TierConfig` and `resolve_tier_model()`**

Cover:
- `Config().tiers` exists
- `[llm.tiers]` TOML parsing works
- `light` auto-detection works for anthropic/openai/google
- `standard` inherits `cfg.llm.model`
- explicit overrides win

**Step 2: Run the focused test**

Run:
```bash
python -m pytest tests/test_subagent_tier_config.py -q
```

Expected: FAIL

**Step 3: Implement `TierConfig` and `resolve_tier_model()`**

In `archon/config.py`:
- add `TierConfig`
- add `tiers: TierConfig` to `Config`
- parse `[llm.tiers]` under the existing `[llm]` block
- add `resolve_tier_model(config, tier)`

**Step 4: Re-run the focused test**

Run:
```bash
python -m pytest tests/test_subagent_tier_config.py -q
```

Expected: PASS

**Step 5: Commit**

```bash
git add archon/config.py tests/test_subagent_tier_config.py
git commit -m "feat: add native subagent tier config"
```

### Task 2: Extract shared non-streaming LLM retry/timeout helper

**Files:**
- Create: `archon/execution/llm_runtime.py`
- Modify: `archon/agent.py`
- Modify: `tests/test_agent.py`
- Create: `tests/test_llm_runtime.py`

**Step 1: Write failing tests for the shared helper**

Cover:
- transient provider errors retry and then succeed
- timeout is enforced
- non-transient failures do not retry endlessly

**Step 2: Run the focused tests**

Run:
```bash
python -m pytest tests/test_llm_runtime.py tests/test_agent.py -q -k 'retry or timeout or llm_runtime'
```

Expected: FAIL because the shared helper module does not exist yet

**Step 3: Implement the shared helper**

Move the current non-streaming retry/timeout logic out of `archon/agent.py` into `archon/execution/llm_runtime.py`.

Requirements:
- preserve current retry delays and transient-error behavior
- preserve current timeout wrapper behavior
- keep the main agent behavior unchanged after the extraction

**Step 4: Update `archon/agent.py` to use the shared helper**

Replace the private direct implementation with imports from `archon/execution/llm_runtime.py`.

**Step 5: Re-run the focused tests**

Run:
```bash
python -m pytest tests/test_llm_runtime.py tests/test_agent.py -q -k 'retry or timeout or llm_runtime'
```

Expected: PASS

**Step 6: Commit**

```bash
git add archon/execution/llm_runtime.py archon/agent.py tests/test_llm_runtime.py tests/test_agent.py
git commit -m "refactor: share llm retry and timeout helper"
```

### Task 3: Extract shared tool-result history shaping

**Files:**
- Create: `archon/execution/history_shaping.py`
- Modify: `archon/agent.py`
- Modify: `tests/test_agent.py`
- Create: `tests/test_history_shaping.py`

**Step 1: Write failing tests for shared shaping helpers**

Cover:
- shell history shaping includes command, exit code, and summarized output
- read-file shaping keeps path/offset/limit plus excerpt
- sampled tools (`list_dir`, `glob`, `grep`) keep counts and samples
- generic truncation still applies

**Step 2: Run the focused tests**

Run:
```bash
python -m pytest tests/test_history_shaping.py tests/test_agent.py -q -k 'shape or history'
```

Expected: FAIL

**Step 3: Implement the shared shaping module**

Extract the logic behind `Agent._shape_tool_result_for_history()` into pure helpers that accept:
- config-like caps
- tool name
- tool args
- result text

**Step 4: Update `archon/agent.py` to delegate to the shared helpers**

Keep behavior identical for the main agent.

**Step 5: Re-run the focused tests**

Run:
```bash
python -m pytest tests/test_history_shaping.py tests/test_agent.py -q -k 'shape or history'
```

Expected: PASS

**Step 6: Commit**

```bash
git add archon/execution/history_shaping.py archon/agent.py tests/test_history_shaping.py tests/test_agent.py
git commit -m "refactor: share tool result history shaping"
```

### Task 4: Add subagent type definitions and an explicit empty registry constructor

**Files:**
- Create: `archon/subagents/__init__.py`
- Create: `archon/subagents/types.py`
- Modify: `archon/tools.py`
- Create: `tests/test_subagent_types.py`
- Create: `tests/test_subagent_registry.py`

**Step 1: Write failing tests for subagent types**

Cover:
- `explore` and `general` definitions exist
- `explore` uses `light`, `general` uses `standard`
- `explore` allowlist contains only read-oriented local tools
- `general` excludes `spawn_subagent`, `delegate_code_task`, and `worker_*`

**Step 2: Write failing tests for `ToolRegistry.empty(...)`**

Cover:
- returned registry has the same internal fields needed by tool registration
- it does not auto-register builtins
- registering tools against it works normally

**Step 3: Run the focused tests**

Run:
```bash
python -m pytest tests/test_subagent_types.py tests/test_subagent_registry.py -q
```

Expected: FAIL

**Step 4: Implement subagent type definitions**

In `archon/subagents/types.py` define:
- `explore`
- `general`

Requirements:
- `explore` allow only `read_file`, `grep`, `glob`, `list_dir`, `shell`
- `general` allow bounded local execution tools only
- do not include worker/session tools, call tools, or `spawn_subagent`

**Step 5: Add `ToolRegistry.empty(...)`**

In `archon/tools.py`, add a first-class constructor that initializes registry state without calling `_register_builtins()`.

Do not use raw `ToolRegistry.__new__` in feature code.

**Step 6: Re-run the focused tests**

Run:
```bash
python -m pytest tests/test_subagent_types.py tests/test_subagent_registry.py -q
```

Expected: PASS

**Step 7: Commit**

```bash
git add archon/subagents/__init__.py archon/subagents/types.py archon/tools.py tests/test_subagent_types.py tests/test_subagent_registry.py
git commit -m "feat: add native subagent types and empty registry constructor"
```

### Task 5: Build fresh filtered registries for subagents

**Files:**
- Create: `archon/subagents/registry.py`
- Modify: `tests/test_subagent_registry.py`

**Step 1: Write failing registry-behavior tests**

Extend the registry tests to prove:
- tools are freshly registered against the child registry
- explore shell rejects dangerous commands through the wrapped confirmer
- general registry excludes `delegate_code_task` and all `worker_*` tools
- no `spawn_subagent` is present in either subagent registry

**Step 2: Run the focused tests**

Run:
```bash
python -m pytest tests/test_subagent_registry.py -q
```

Expected: FAIL

**Step 3: Implement `build_subagent_registry(...)`**

Requirements:
- start from `ToolRegistry.empty(...)`
- re-register tools onto the new registry instance
- wrap explore confirmer so `Level.DANGEROUS` / `Level.FORBIDDEN` are rejected
- do not register worker/session tools or `spawn_subagent`

**Step 4: Re-run the focused tests**

Run:
```bash
python -m pytest tests/test_subagent_registry.py -q
```

Expected: PASS

**Step 5: Commit**

```bash
git add archon/subagents/registry.py tests/test_subagent_registry.py
git commit -m "feat: build fresh registries for native subagents"
```

### Task 6: Implement the bounded subagent runner with policy and parity helpers

**Files:**
- Create: `archon/subagents/runner.py`
- Create: `tests/test_subagent_runner.py`

**Step 1: Write failing runner tests**

Cover:
- simple text completion
- tool call then completion
- repeated tool errors stop early
- wall-clock timeout returns `status=timeout`
- iteration limit returns `status=iteration_limit`
- `SuspensionRequest` becomes `status=failed`
- dangerous tools denied by runtime policy are surfaced as failures/tool errors
- shaped tool results, not raw long outputs, are appended back into history

**Step 2: Run the focused tests**

Run:
```bash
python -m pytest tests/test_subagent_runner.py -q
```

Expected: FAIL

**Step 3: Implement `SubagentRunner`**

Requirements:
- use the shared non-streaming LLM retry/timeout helper from Task 2
- use the shared history-shaping helper from Task 3
- call `evaluate_tool_policy(..., profile_name="default")` before tool execution
- fail fast on `SuspensionRequest`
- return structured `SubagentResult`

Do not call raw `llm.chat(...)` directly in the loop.

**Step 4: Re-run the focused tests**

Run:
```bash
python -m pytest tests/test_subagent_runner.py -q
```

Expected: PASS

**Step 5: Commit**

```bash
git add archon/subagents/runner.py tests/test_subagent_runner.py
git commit -m "feat: add bounded native subagent runner"
```

### Task 7: Register `spawn_subagent` and emit usage events

**Files:**
- Create: `archon/subagents/tools.py`
- Modify: `archon/subagents/__init__.py`
- Modify: `archon/tools.py`
- Create: `tests/test_subagent_tools.py`
- Create: `tests/test_subagent_integration.py`

**Step 1: Write failing tool tests**

Cover:
- `spawn_subagent` is registered in the default parent registry
- invalid type returns a direct error string
- empty task returns a direct error string
- explore/general build the correct tier model and registry
- result formatting includes type/status/iterations/tokens
- a completed subagent emits a `subagent_usage` event payload

**Step 2: Run the focused tests**

Run:
```bash
python -m pytest tests/test_subagent_tools.py tests/test_subagent_integration.py -q
```

Expected: FAIL

**Step 3: Implement `register_subagent_tools(...)`**

Requirements:
- resolve tier model
- create fresh child registry
- instantiate child `LLMClient`
- run the child runner
- format result text
- emit `subagent_usage` through the registry execute-event path

**Step 4: Wire it into `ToolRegistry._register_builtins()`**

Register `spawn_subagent` in the parent registry only.

**Step 5: Re-run the focused tests**

Run:
```bash
python -m pytest tests/test_subagent_tools.py tests/test_subagent_integration.py -q
```

Expected: PASS

**Step 6: Commit**

```bash
git add archon/subagents/tools.py archon/subagents/__init__.py archon/tools.py tests/test_subagent_tools.py tests/test_subagent_integration.py
git commit -m "feat: add spawn_subagent tool"
```

### Task 8: Roll subagent usage into parent `/cost`

**Files:**
- Modify: `archon/agent.py`
- Create: `tests/test_subagent_token_rollup.py`

**Step 1: Write failing usage-rollup tests**

Cover:
- `subagent_usage` increments `total_input_tokens` / `total_output_tokens`
- a ledger event is recorded for the parent session with source `subagent:<type>`
- `/cost`-style workflow totals will therefore include native subagent usage

**Step 2: Run the focused tests**

Run:
```bash
python -m pytest tests/test_subagent_token_rollup.py -q
```

Expected: FAIL

**Step 3: Implement parent event handling**

In `archon/agent.py`, extend `_on_tool_execute_event()` to handle `subagent_usage` by:
- updating counters
- writing a normalized usage ledger event for the parent session
- leaving normal tool UX handling unchanged

**Step 4: Re-run the focused tests**

Run:
```bash
python -m pytest tests/test_subagent_token_rollup.py -q
```

Expected: PASS

**Step 5: Commit**

```bash
git add archon/agent.py tests/test_subagent_token_rollup.py
git commit -m "feat: account for native subagent token usage"
```

### Task 9: Final verification

**Files:**
- Verify only

**Step 1: Run the native-subagent test slice**

Run:
```bash
python -m pytest \
  tests/test_subagent_tier_config.py \
  tests/test_llm_runtime.py \
  tests/test_history_shaping.py \
  tests/test_subagent_types.py \
  tests/test_subagent_registry.py \
  tests/test_subagent_runner.py \
  tests/test_subagent_tools.py \
  tests/test_subagent_integration.py \
  tests/test_subagent_token_rollup.py \
  -q
```

Expected: PASS

**Step 2: Run the broader regression slice**

Run:
```bash
python -m pytest tests/test_agent.py tests/test_tools.py tests/test_cli.py tests/test_config.py -q
```

Expected: PASS

**Step 3: Run the full suite**

Run:
```bash
python -m pytest tests -q
```

Expected: PASS

**Step 4: Review the final diff**

Run:
```bash
git diff --stat HEAD~8..HEAD
```

Confirm:
- the change stayed within the native-subagent scope
- no worker/session behavior regressed
- shared helper extraction stayed narrow

**Step 5: Commit any final test-only adjustments if needed**
