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

A lightweight wrapper around the existing `execute_turn()` function. It does NOT reuse `Agent` — it creates only what's needed:

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

### What SubagentRunner creates

- Fresh `history` list with `[{"role": "user", "content": task + context}]`
- `llm_step` and `llm_step_no_tools` callables — closures over the subagent's `LLMClient` and history, passed as explicit keyword arguments to `execute_turn()`
- A minimal agent-like shim object that `execute_turn()` can operate on. The turn executor accesses many attributes and methods on the agent object. The shim must provide:

**Required attributes:**
- `history` — the fresh message list
- `config` — parent's Config (for policy evaluation)
- `tools` — the filtered ToolRegistry
- `max_iterations` — from SubagentConfig
- `wall_clock_timeout_sec` — from SubagentConfig
- `max_consecutive_tool_errors` — hardcoded (3)
- `total_input_tokens`, `total_output_tokens` — counters (start at 0)
- `on_thinking` — `None` (no UI callback)
- `on_tool_call` — `None` (no UI callback)
- `last_turn_id` — static string like `"subagent"`
- `last_suspension_request` — `None`
- `diagnostic_tool_error_threshold` — hardcoded (2)

**Required methods (with subagent-appropriate implementations):**
- `_make_assistant_msg(response)` — same as Agent's (converts LLMResponse to history message)
- `_emit_hook(kind, payload)` — no-op (parent handles hooks)
- `_record_llm_usage(turn_id, source, response)` — no-op (token tracking via attributes)
- `_consume_pending_compactions_into_prompt(prompt)` — returns prompt unchanged (no compaction)
- `_enforce_iteration_budget()` — no-op (subagents are short-lived, no budget enforcement)
- `_shape_tool_result_for_history(tool_name, tool_args, result_text)` — truncation logic (can reuse Agent's or simplified version)

**Note:** `log_label` and `terminal_activity_feed` are accessed via `getattr()` with defaults in print helpers — safe to omit from the shim.

### What SubagentRunner does NOT have

- History trimming (short-lived, won't hit context limits)
- Compaction — `_consume_pending_compactions_into_prompt()` is a no-op that returns the prompt unchanged
- Skills or profiles (subagents are task-focused)
- Hook bus — `_emit_hook()` is a no-op
- Activity summary or memory prefetch
- Session ID or session persistence
- LLM usage recording — `_record_llm_usage()` is a no-op; token counts tracked via `total_input_tokens`/`total_output_tokens` attributes and rolled into parent after run

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

### General Type (full minus nesting)

Allowed tools:
- Everything the parent has registered, EXCEPT `spawn_subagent`

Uses the parent's `confirmer` for safety gates.

### Implementation

Tool filtering happens at registry construction time. For each subagent type, we build a new `ToolRegistry` and copy only the allowed tools from the parent registry:

```python
def build_subagent_registry(
    parent_registry: ToolRegistry,
    subagent_type: str,
    confirmer: Callable | None = None,
) -> ToolRegistry:
```

For `explore`, the shell tool's confirmer is wrapped to reject non-SAFE commands.

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
4. Build filtered `ToolRegistry` for the type
5. Create `LLMClient` with tier model (reuses parent's API key, provider, base_url)
6. Create `SubagentConfig` and `SubagentRunner`
7. Call `runner.run()`
8. Format `SubagentResult` as tool result string
9. Add subagent's token usage to parent's totals

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
| `archon/execution/turn_executor.py` | No changes — reused as-is |
| `archon/agent.py` | Token rollup: add subagent tokens to `total_input_tokens`/`total_output_tokens` |

## Error Handling

| Scenario | Behavior |
|----------|----------|
| LLM API failure | `execute_turn()` handles retries. Subagent returns `status="failed"` with error. |
| Repeated tool errors | `execute_turn()`'s `consecutive_tool_errors` logic stops the subagent. Returns `status="failed"`. |
| Iteration limit hit | `execute_turn()` calls `_finalize_without_tools()`. Returns `status="iteration_limit"` with summary. |
| Wall-clock timeout | `execute_turn()` handles this. Returns `status="timeout"`. |
| Invalid subagent type | Tool handler returns error string immediately. No subagent spawned. |
| Empty task | Tool handler returns error string immediately. |

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
5. Token usage from subagents appears in `/cost` output
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
