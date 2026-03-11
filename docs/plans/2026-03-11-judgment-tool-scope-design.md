# Judgment Tool Scope Design

**Date:** 2026-03-11
**Branch:** `judgment-tool-scope`
**Status:** Approved for implementation

## Problem

Archon's control plane already resolves skills and profiles into an effective policy, but the model still receives the broad default tool schema in the main agent loop. That means skill selection improves prompts and execution-time policy, yet the model can still plan against tools it should not see. The result is weaker judgment, wasted tool attempts, and avoidable policy denials.

The current system has three layers:
- `archon/control/skills.py` resolves a profile into `ResolvedSkillProfile`
- `archon/control/policy.py` enforces tool and MCP access at execution time
- `archon/agent.py` still passes `self.tools.get_schemas()` directly into the LLM call path

This slice fixes the mismatch between visible capability and allowed capability.

## Goals

- Make the LLM-visible tool schema match the resolved profile/skill tool scope.
- Keep execution-time policy enforcement as the hard backstop.
- Preserve default-profile behavior when `allowed_tools = ["*"]`.
- Keep the change small: no hybrid-runtime rewrite, no small-model routing, no tool implementation churn unless required for correctness.

## Non-Goals

- Rebuilding `hybrid` into a true planner/controller runtime.
- Adding new tools or new skills.
- Changing permission-mode semantics.
- Introducing a second LLM for routing or judgment.

## Chosen Approach

Use `visible-scope + runtime enforcement`.

The agent will compute an effective schema list per turn based on the resolved profile. That filtered list becomes the only tool surface shown to the LLM. Existing runtime checks in `execute_turn()` / `execute_turn_stream()` remain unchanged so policy still denies any unexpected or forged tool call.

This gives us two aligned layers:
- soft guidance becomes real because the model only sees allowed tools
- hard enforcement remains intact if something slips through

## Architecture

### 1. Tool schema filtering

Add a narrow filtering API to `ToolRegistry` so the registry can return:
- full schema list for unrestricted profiles
- filtered schema list for constrained profiles

This should be based on resolved profile capabilities, not ad-hoc string matching in the agent.

Expected behavior:
- `*` exposes the full registry
- explicit allowed tools expose only those tool schemas
- `mcp_call` is visible only when the profile allows `mcp`, `mcp:<server>`, or `*`
- unknown allowed tool names do not cause failures; they are ignored at schema-build time

### 2. Agent turn wiring

Update `archon/agent.py` so both `run()` and `run_stream()` use the filtered schema list for the active profile instead of the full registry. Native capability shortcuts like news handling stay outside this path because they are direct product behavior, not model tool-choice behavior.

### 3. Truthful runtime summary

`build_runtime_capability_summary()` already reports effective tool scope using resolved profile data. That is good. After this slice, the reported scope and the model-visible scope will finally match.

### 4. Enforcement remains unchanged

`archon/execution/turn_executor.py` keeps its current policy checks:
- `evaluate_tool_policy()` for normal tools
- `evaluate_mcp_policy()` for `mcp_call`

This is important because schema filtering improves judgment but should not be the only safety layer.

## Testing Strategy

Add focused regression tests for:
- schema filtering on `ToolRegistry`
- active skill/profile causing filtered schema delivery to the LLM in `Agent.run()`
- MCP visibility alignment for profiles that allow only `mcp_call` or `mcp:<server>`
- default unrestricted behavior preserving full schema delivery
- runtime deny path still blocking disallowed tools even if a tool call is injected manually in tests

Baseline evidence before implementation:
- `pytest -q tests/test_agent.py tests/test_config.py tests/test_cli.py -k 'skill or policy or permissions or orchestrator'`
- Result: `60 passed`

## Risks

### Risk 1: Schema filtering diverges from runtime policy
Mitigation: keep filtering logic minimal and derive it from the same resolved profile semantics used by policy. Tests should cover both visible schema and runtime deny behavior.

### Risk 2: MCP visibility becomes too strict or too loose
Mitigation: treat `mcp_call` as a special case with explicit tests for `*`, `mcp`, and `mcp:<server>` allowances.

### Risk 3: Breaking default behavior
Mitigation: add an unrestricted-profile test proving the full schema still reaches the model unchanged.

## Expected Outcome

After this slice:
- active skills will materially constrain the model's planning space
- Archon should waste fewer iterations on disallowed tools
- `/permissions` and runtime capability summaries will better reflect actual behavior
- the control plane becomes more real without a broad architecture rewrite
