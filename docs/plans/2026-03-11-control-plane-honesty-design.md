# Control Plane Honesty Design

**Date:** 2026-03-11
**Branch:** `control-plane-honesty`
**Status:** Approved for implementation

## Problem

Archon's execution core is stronger than its user-facing control-plane language. The current `hybrid` mode mostly routes into the same shared executor path as legacy mode, but several status, route, and help surfaces can imply a more distinct planner/runtime architecture than the code actually provides. At the same time, some bounded native capabilities such as AI news, jobs, and research-status inspection can still fall through into generic LLM behavior instead of being handled directly.

The result is a trust gap:
- status and route labels are not always as precise as the runtime can prove
- built-in capabilities are not always preferred over generic reasoning
- terminal and Telegram can feel slightly inconsistent even when the underlying capability already exists

This slice fixes the control-plane honesty layer without changing the underlying execution architecture.

## Goals

- Make `hybrid` surfaces truthful without renaming the config/runtime knob.
- Centralize user-facing wording for orchestrator mode and route-path descriptions.
- Prefer native built-in capabilities for bounded requests such as AI news, jobs, and research status before generic LLM fallthrough.
- Keep terminal and Telegram behavior aligned where parity is intended.
- Add focused regression coverage for the affected surfaces.

## Non-Goals

- Replacing `hybrid` with a new runtime architecture.
- Renaming config keys or CLI commands purely for aesthetics.
- Adding a small-model router.
- Broad UX redesign outside the honesty/native-routing slice.
- Changing worker, MCP, or deep-research internals unless required for truthful reporting.

## Chosen Approach

Use `stable names, truthful semantics`.

The system will keep `hybrid` as the configuration term, but every user-facing description of that mode will be narrowed to what the code can actually prove today: a shared executor path with routing/policy overlays. Separately, native built-in requests will be recognized earlier and handled more directly so the assistant does not waste turns or misroute bounded requests into generic search/chat behavior.

This is the smallest change that increases user trust without introducing migration churn.

## Architecture

### 1. Centralize orchestrator mode wording

Today, orchestrator wording is built in multiple places, including CLI status/help text and route hook metadata. This slice should introduce a small shared description layer so surfaces stop inventing their own phrasing.

Expected behavior:
- config/runtime mode remains `legacy` or `hybrid`
- user-facing text for `hybrid` should clarify `shared executor`
- route metadata paths should describe what actually happened, not what might exist in a future architecture
- terminal summaries, help text, and any surfaced route labels should use the same vocabulary

This should live in the control/CLI layer, not scattered through tests and string literals.

### 2. Tighten native capability preference

Archon already has direct product capabilities for:
- `news_brief`
- job listing and job inspection
- research status inspection
- several bounded slash-command flows

This slice should make sure bounded requests for these paths are recognized before generic LLM reasoning when appropriate. The system should only fall through to the general agent loop when the request is genuinely ambiguous or open-ended.

Expected behavior:
- explicit AI-news asks prefer the built-in digest path
- explicit job/research status asks prefer native job/status handlers
- Telegram and terminal should converge on the same routing preference where the intent is the same

### 3. Keep execution architecture unchanged

The underlying executor behavior is intentionally not changing.

`hybrid` still routes through the current shared executor path. This design does not attempt to invent a planner layer. The honesty improvement comes from better descriptions and better routing preference, not from pretending the runtime is more advanced than it is.

### 4. Testing strategy

Add focused tests for:
- `/status` and `/help` honesty wording
- route metadata wording for legacy vs hybrid shared-executor paths
- native preference for AI news and bounded job/research status requests
- parity-sensitive terminal/Telegram cases where existing behavior should stay aligned

The tests should stay behavior-focused. They should prove what Archon says and what path it takes, not just snapshot arbitrary strings.

## Risks

### Risk 1: Over-correcting user-facing wording
Mitigation: keep the config/runtime names stable and narrow the change to descriptors, not identifiers.

### Risk 2: Native routing becomes too aggressive
Mitigation: restrict the shortcut logic to explicit bounded intents and preserve generic fallthrough for ambiguous requests.

### Risk 3: Terminal and Telegram drift again
Mitigation: use shared helpers where possible and cover at least one cross-surface parity case in tests.

## Expected Outcome

After this slice:
- Archon should describe `hybrid` more honestly
- status/help output should better match real runtime behavior
- bounded built-in requests should hit native paths more reliably
- the control plane should feel more trustworthy without increasing system complexity
