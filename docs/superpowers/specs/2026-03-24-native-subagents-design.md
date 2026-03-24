# Native Subagent System Design

## Problem

Archon can only delegate tasks by shelling out to external CLI tools (Codex, Claude Code, OpenCode). This means:

- No lightweight exploration — every delegation boots a full CLI session
- No model tiering — can't route cheap tasks to Haiku
- No context isolation — can't offload research to a separate context window
- External dependency — workers only work if CLI tools are installed
- High latency — CLI boot, auth, config loading adds seconds per delegation

Claude Code and Codex both solve this with native subagents: fresh LLM context + restricted tools + model routing, all in-process.

## Goals

1. Add a `spawn_subagent` tool that runs a lightweight agent loop in-process with a fresh context window
2. Support two subagent types: `explore` (Haiku, read-only) and `general` (inherited model, full tools)
3. Add model tier configuration (`[llm.tiers]`) reusable by compressor/distiller/activity-summarizer
4. Coexist with the existing external worker system — native subagents for lightweight tasks, external workers for heavy sandboxed work
5. No nesting — subagents cannot spawn subagents

## Non-Goals

- Background/parallel subagent execution (v2)
- Git worktree isolation (v2, if needed for parallel general subagents)
- Auto-routing via orchestrator (the LLM decides when to spawn)
- Replacing the external worker system
- Subagent-to-subagent communication
- Persistent subagent sessions / resumption

## Architecture

```
Parent Agent (Sonnet, full tools, user's conversation)
  │
  ├─ spawn_subagent(type="explore", task="find all config parsing")
  │     ├─ LLMClient(haiku)
  │     ├─ ToolRegistry(read-only subset)
  │     ├─ execute_turn(fresh_history, max_iterations=8)
  │     └─ return final_text[:3000]  → condensed into parent context
  │
  ├─ spawn_subagent(type="general", task="refactor load_config()")
  │     ├─ LLMClient(inherited sonnet)
  │     ├─ ToolRegistry(full set minus spawn_subagent)
  │     ├─ execute_turn(fresh_history, max_iterations=12)
  │     └─ return final_text[:3000]
  │
  └─ delegate_code_task(...)  → external workers still available
```

The subagent runs in the parent's thread (foreground, blocking). The parent's turn pauses while the subagent executes, then resumes with the subagent's result as the tool output.

No nesting is enforced by tool filtering: `spawn_subagent` is never registered in a subagent's `ToolRegistry`.

## Model Tiers

### Configuration

New `[llm.tiers]` section in `config.toml`:

```toml
[llm.tiers]
light = ""      # empty = auto-detect from provider
standard = ""   # empty = use main [llm].model
```

### Auto-Detection (when `light` is empty)

| Provider | Light model |
|----------|-------------|
| Anthropic | `claude-haiku-4-5-20251001` |
| OpenAI | `gpt-4o-mini` |
| Google | `gemini-2.5-flash` |

When `standard` is empty, it inherits the main `[llm].model`.

### Tier Mapping

| Subagent type | Tier | Default model |
|---------------|------|---------------|
| `explore` | `light` | Haiku |
| `general` | `standard` | Parent's model |

### Reusability

The tier config is designed for reuse by other internal LLM callers:
- Compressor (`archon/compressor.py`) → `light`
- Distiller (`archon/distiller.py`) → `light`
- Activity summarizer v2 → `light`
- News summarizer → `light`

These are not part of this spec but the config structure supports them.

### Config Dataclass

```python
@dataclass
class TierConfig:
    light: str = ""      # resolved at runtime
    standard: str = ""   # resolved at runtime
```

Added to the main `Config` as `tiers: TierConfig`. Parsed from `[llm.tiers]` in `load_config()`.

Resolution function:

```python
def resolve_tier_model(config: Config, tier: str) -> str:
    """Return the model string for a tier, applying auto-detection if empty."""
```

## SubagentRunner

### Location

`archon/subagents/runner.py`

### Design

A lightweight wrapper that runs the subagent's agent loop. It does NOT reuse `Agent` — it creates only what's needed.

**Why not reuse `execute_turn()` directly:** `execute_turn()` returns a plain `str` or `SuspensionRequest`. Timeouts and iteration limits are surfaced as normal assistant text via `_finalize_without_tools()`, with no structured status channel or iteration count. The `SubagentResult` contract requires `status` (ok/failed/timeout/iteration_limit) and `iterations_used`.

**Solution:** `SubagentRunner.run()` implements its own iteration loop modeled on `execute_turn()` but adapted for subagent needs:
- Same pattern: `for iteration in range(max_iterations): call LLM → process tool calls → repeat`
- Reuses `_chat_with_retry()` for the LLM step (or direct `LLMClient` calls)
- Reuses `ToolRegistry.execute()` for tool execution
- Tracks iteration count and detects timeout/iteration-limit/failure as structured `SubagentResult.status`
- Does NOT reuse `execute_turn()` as a black box — it is a simplified, purpose-built loop

This is ~80 lines of straightforward loop code. The simplification vs `execute_turn()`: no streaming path, no suspension requests, no skill guidance, no compaction injection, no hook emission. Just: LLM call → tool execution → repeat → return structured result.

```python
@dataclass
class SubagentConfig:
    type: str              # "explore" | "general"
    task: str              # the user's prompt
    context: str           # optional extra context
    max_iterations: int    # 8 for explore, 12 for general
    max_result_chars: int  # 3000 default
    model: str             # resolved from tier
    system_prompt: str     # type-specific
    wall_clock_timeout_sec: float  # 60 for explore, 300 for general

@dataclass
class SubagentResult:
    status: str            # "ok" | "failed" | "timeout" | "iteration_limit"
    text: str              # final message, truncated to max_result_chars
    input_tokens: int
    output_tokens: int
    iterations_used: int

class SubagentRunner:
    def __init__(
        self,
        config: SubagentConfig,
        llm: LLMClient,
        tools: ToolRegistry,
    ):
        self.config = config
        self.llm = llm
        self.tools = tools

    def run(self) -> SubagentResult:
        """Execute the subagent task and return a condensed result."""
```

### What SubagentRunner manages

Since the runner implements its own loop (not delegating to `execute_turn()`), it does not need a duck-typed agent shim. It manages:

- `history: list[dict]` — fresh message list, starts with `[{"role": "user", "content": task + context}]`
- `tools: ToolRegistry` — the filtered registry (freshly built, not copied)
- `llm: LLMClient` — tier-appropriate client
- `total_input_tokens`, `total_output_tokens` — counters (start at 0)
- `iterations_used: int` — tracks loop progress

**Helper functions reused from Agent (extracted or duplicated):**
- `_make_assistant_msg(response)` — converts `LLMResponse` to Anthropic-format history message. This is a pure function (~10 lines) that can be extracted from `Agent` or duplicated in the runner.
- `_shape_tool_result_for_history(tool_name, tool_args, result_text)` — truncation logic for tool results. Same approach: extract or duplicate.
- Tool policy evaluation — the runner calls `evaluate_tool_policy()` directly (it's already a standalone function in `archon/control/policy.py`), passing `profile_name="default"`.
- Secret redaction — `redact_secret_like_text()` is a standalone function, called on tool results before adding to history.

### Profile Handling

`execute_turn()` requires an `active_profile` parameter and uses it for tool policy evaluation (`evaluate_tool_policy()` in `turn_executor.py`). Subagents cannot simply skip this.

**Solution:** The subagent shim passes `"default"` as `active_profile`. This is safe because:
1. Tool visibility is already controlled by which tools are registered in the subagent's fresh registry (not by profile filtering)
2. The `"default"` profile's policy evaluation is permissive — it does not restrict tools beyond what the registry exposes
3. The explore safety override is handled at the registry's `confirmer` level, not the profile level

The shim's `_visible_tool_schemas()` method (if accessed) returns all schemas from the subagent's registry — profile-based filtering is not applied since the registry already contains only the allowed tools.

### What SubagentRunner does NOT have

- History trimming (short-lived, won't hit context limits)
- Compaction — `_consume_pending_compactions_into_prompt()` is a no-op that returns the prompt unchanged
- Skills — subagents are task-focused; no skill guidance is injected into the system prompt
- Hook bus — `_emit_hook()` is a no-op
- Activity summary or memory prefetch
- Session ID or session persistence
- LLM usage recording within the runner loop — the runner tracks tokens via counters, not per-call ledger writes

### What SubagentRunner passes through

- Safety confirmation (`confirmer`) from the parent's registry — for `general` type
- Token usage tracking — rolled into parent's totals after run completes

## Tool Filtering

### Explore Type (read-only)

Allowed tools:
- `read_file`
- `grep`
- `glob`
- `shell` — with a safety override: confirmer rejects anything classified as `DANGEROUS` or `FORBIDDEN`. Only `SAFE` read-only commands pass.

Not allowed:
- `write_file`, `edit_file`
- `spawn_subagent`
- `delegate_code_task`, all worker tools
- Memory tools
- MCP tools
- Setup tools
- Call/mission tools

### General Type (full minus nesting and persistent work)

Allowed tools:
- All `register_*_tools()` functions are called EXCEPT:
  - `register_subagent_tools()` — prevents nesting
  - `register_worker_tools()` — prevents spawning durable background worker sessions (see Implementation section below)

Uses the parent's `confirmer` for safety gates.

### Implementation

**Problem with naive registry copying:** Tool handlers in Archon are closures over the original `ToolRegistry` instance. For example, `shell()` in `filesystem_tools.py` closes over `registry` to access `registry.confirmer`, `registry.archon_source_dir`, and `registry.config`. Worker tool handlers similarly close over their registry. Copying handler functions into a new registry means they still reference the *parent* registry's confirmer and config, breaking the explore safety override.

**Solution: build a fresh registry with re-registered tools.** Instead of copying handlers, we construct a new `ToolRegistry` and call the same `register_*_tools()` functions that `_register_builtins()` uses, but only the ones appropriate for the subagent type. The new registry gets its own `confirmer` (overridden for explore) and `config`, so closures bind to the correct instance.

```python
def build_subagent_registry(
    parent_config: Config,
    subagent_type: str,
    confirmer: Callable | None = None,
    archon_source_dir: str | None = None,
) -> ToolRegistry:
    """Build a fresh ToolRegistry for a subagent by calling registration
    functions directly, so handler closures bind to the new registry."""
```

For `explore`:
- Call only `register_filesystem_tools(registry)` (provides shell, read_file, grep, glob)
- The registry's `confirmer` is wrapped: rejects anything classified as `DANGEROUS` or `FORBIDDEN`, only passes `SAFE` commands through
- Do NOT call `register_worker_tools`, `register_memory_tools`, `register_mcp_tools`, etc.

For `general`:
- Call all `register_*_tools()` functions EXCEPT `register_subagent_tools()` (prevents nesting)
- Also exclude `register_worker_tools()` to prevent spawning durable background worker sessions (see below)
- Uses parent's `confirmer` unchanged

**Why exclude worker tools from general subagents:** The spec's non-goals include "persistent subagent sessions / resumption." Worker tools like `worker_start` default to background=True and create durable sessions that outlive the subagent's turn. A short-lived native subagent should not spawn persistent background work. If the parent needs heavy delegation, it should use `delegate_code_task` directly.

## The `spawn_subagent` Tool

### Schema

```python
spawn_subagent(
    task: str,              # required — what to do
    type: str = "explore",  # "explore" | "general"
    context: str = "",      # optional extra context
) -> str
```

### Registration

Registered in the parent's `ToolRegistry` via `register_subagent_tools(registry)`, called from `_register_builtins()`.

### Handler Flow

1. Validate `type` is "explore" or "general"
2. Resolve model from tier config
3. Look up type defaults (max_iterations, timeout, system_prompt)
4. Build filtered `ToolRegistry` for the type (fresh registration, not handler copying)
5. Create `LLMClient` with tier model (reuses parent's API key, provider, base_url)
6. Create `SubagentConfig` and `SubagentRunner`
7. Call `runner.run()`
8. Format `SubagentResult` as tool result string
9. Roll up token usage: add to parent's `total_input_tokens`/`total_output_tokens` AND call parent's `_record_llm_usage()` with `source="subagent:{type}"` so ledger-backed `/cost` totals include subagent usage

### System Prompts

**explore**:
```
You are a codebase search assistant. Find the requested information
efficiently using read_file, grep, glob, and shell (read-only commands
only). Return a concise summary of what you found with exact file paths
and line numbers. Do not modify any files.
```

**general**:
```
You are a task execution assistant. Complete the requested task
thoroughly. Return a summary of what you did, what changed, and any
issues encountered.
```

### Result Format

Returned to the parent agent as a tool result string:

```
subagent_type: explore
status: ok
iterations: 3/8
tokens: 1200 in, 450 out

<subagent's final text response, truncated to 3000 chars>
```

## File Structure

```
archon/subagents/
    __init__.py          # public API: SubagentResult, register_subagent_tools
    runner.py            # SubagentRunner, SubagentConfig, SubagentResult
    types.py             # SUBAGENT_TYPES dict: tool allowlists, system prompts, defaults
    tools.py             # register_subagent_tools() — registers spawn_subagent tool
    registry.py          # build_subagent_registry() — filtered tool registry builder
```

### Integration Points

| File | Change |
|------|--------|
| `archon/tools.py` | `_register_builtins()` calls `register_subagent_tools(self)` |
| `archon/config.py` | Add `TierConfig` dataclass, `[llm.tiers]` parsing in `load_config()`, add `tiers` field to `Config` |
| `archon/llm.py` | No changes — instantiate with different model string |
| `archon/execution/turn_executor.py` | No changes — SubagentRunner has its own simplified loop |
| `archon/agent.py` | Token rollup: add subagent tokens to `total_input_tokens`/`total_output_tokens` |

## Error Handling

| Scenario | Behavior |
|----------|----------|
| LLM API failure | `SubagentRunner` catches the exception. Returns `SubagentResult(status="failed", text=error_message)`. |
| Repeated tool errors | Runner tracks `consecutive_tool_errors`. After 3, stops iterating. Returns `status="failed"` with last error. |
| Iteration limit hit | Runner exits loop after `max_iterations`. Returns `status="iteration_limit"` with last assistant text. |
| Wall-clock timeout | Runner checks `time.monotonic()` each iteration. Returns `status="timeout"` with partial result. |
| Invalid subagent type | Tool handler returns error string immediately. No subagent spawned. |
| Empty task | Tool handler returns error string immediately. |
| SuspensionRequest from tool | Runner treats as error — subagents don't support suspension. Returns `status="failed"`. |

All errors are non-fatal to the parent — the tool result contains the error, and the parent agent decides how to proceed.

## Limits & Defaults

| Parameter | Explore | General |
|-----------|---------|---------|
| Max iterations | 8 | 12 |
| Wall-clock timeout | 60s | 300s |
| Max result chars | 3000 | 3000 |
| Model tier | light | standard |

These are hardcoded defaults in `types.py`. Future work could make them configurable via `[subagents.explore]` / `[subagents.general]` config sections if needed.

## Testing Strategy

### Unit Tests

- `test_subagent_runner.py` — `SubagentRunner` with mock `LLMClient` and mock `ToolRegistry`. Test: successful run, iteration limit, timeout, LLM failure, tool errors.
- `test_subagent_types.py` — Verify type definitions: tool allowlists, defaults, system prompts.
- `test_subagent_registry.py` — `build_subagent_registry()` filtering. Verify explore gets read-only tools, general gets everything minus `spawn_subagent`, shell safety override works for explore.
- `test_subagent_tools.py` — `spawn_subagent` tool handler end-to-end with mocked runner. Verify argument validation, tier resolution, result formatting, token rollup.
- `test_tier_config.py` — `TierConfig` parsing, auto-detection per provider, user overrides.

### No real LLM calls in tests

All LLM interactions are mocked. The `execute_turn()` function is tested elsewhere; subagent tests focus on the wrapping, filtering, and integration logic.

## Acceptance Criteria

1. `spawn_subagent(type="explore", task="find X")` runs a Haiku-powered search with read-only tools and returns results to parent
2. `spawn_subagent(type="general", task="do X")` runs with the parent's model and full tools (minus nesting) and returns results
3. Explore subagents cannot write files or execute dangerous commands
4. General subagents cannot spawn sub-subagents
5. Token usage from subagents appears in `/cost` output (both session counters and ledger-backed workflow totals)
6. Model tier auto-detection works for Anthropic, OpenAI, Google providers
7. User can override tier models via `[llm.tiers]` config
8. Subagent failures return error info to parent without crashing the parent's turn
9. Subagent results are truncated to 3000 chars max
10. All existing tests continue to pass (no regressions)
11. External worker system (`delegate_code_task`) continues to work unchanged

## Rollout

1. Feature lands behind no flag — always available once merged
2. `spawn_subagent` appears in tool list alongside existing tools
3. System prompt guidance added so the agent knows when to use `spawn_subagent` vs `delegate_code_task` vs doing work inline
4. No breaking changes to existing behavior
