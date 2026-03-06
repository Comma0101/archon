# Super Assistant Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn Archon into a lightweight two-surface super assistant by adding a real router, first-class skills, shared job state, and layered memory without breaking the current direct agent path.

**Architecture:** Build on the existing control/execution migration instead of rewriting the agent loop. Add a three-lane router in the control plane, treat skills as routing profiles, normalize long-running work into shared jobs, and compact long histories into layered memory. Keep terminal and Telegram as equal first-class surfaces over the same controller.

**Tech Stack:** Python 3.11+, existing Archon control/execution modules, current CLI and Telegram adapters, pytest.

---

### Task 1: Add Route Lane Contracts And Hook Payloads

**Files:**
- Modify: `archon/control/contracts.py`
- Modify: `archon/control/orchestrator.py`
- Modify: `archon/control/hooks.py`
- Test: `tests/test_agent.py`

**Step 1: Write the failing test**

Add tests that assert hybrid orchestration emits a route decision containing:
- `lane` (`fast`, `operator`, or `job`)
- `reason`
- `surface`
- `skill`

Use existing orchestrator hook tests as the starting point.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_agent.py -q -k "orchestrator and lane"`
Expected: FAIL because lane metadata is not present.

**Step 3: Write minimal implementation**

- Add route/lane fields to control contracts.
- Extend `orchestrate_response()` and `orchestrate_stream_response()` to emit route payloads with lane metadata.
- Keep execution behavior unchanged: hybrid still falls back to the existing direct path.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_agent.py -q -k "orchestrator and lane"`
Expected: PASS.

**Step 5: Commit**

```bash
git add archon/control/contracts.py archon/control/orchestrator.py archon/control/hooks.py tests/test_agent.py
git commit -m "feat: add lane-aware route metadata to orchestrator hooks"
```

### Task 2: Implement A Minimal Three-Lane Classifier

**Files:**
- Modify: `archon/control/orchestrator.py`
- Modify: `archon/control/session_controller.py`
- Test: `tests/test_agent.py`

**Step 1: Write the failing test**

Add tests for route classification:
- simple chat -> `fast`
- bounded file/status request -> `operator`
- broad/deep/delegated request -> `job`

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_agent.py -q -k "route classifier"`
Expected: FAIL because classifier logic does not exist.

**Step 3: Write minimal implementation**

- Add a small deterministic classifier in `archon/control/orchestrator.py`.
- Reuse `archon/control/session_controller.py` heuristics where helpful.
- Keep logic explicit and keyword/rule based at first; do not introduce LLM planning here.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_agent.py -q -k "route classifier"`
Expected: PASS.

**Step 5: Commit**

```bash
git add archon/control/orchestrator.py archon/control/session_controller.py tests/test_agent.py
git commit -m "feat: add minimal three-lane task classifier"
```

### Task 3: Add Skill Registry And Skill-Backed Profiles

**Files:**
- Create: `archon/control/skills.py`
- Modify: `archon/config.py`
- Modify: `archon/control/policy.py`
- Modify: `archon/prompt.py`
- Test: `tests/test_config.py`
- Test: `tests/test_agent.py`

**Step 1: Write the failing test**

Add tests that prove:
- default built-in skills load (`general`, `coder`, `researcher`, `operator`, `sales`, `memory_curator`)
- a skill resolves to allowed tools and preferred model metadata
- policy can evaluate a skill-backed profile

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py tests/test_agent.py -q -k "skill or profile"`
Expected: FAIL because skill registry/config does not exist.

**Step 3: Write minimal implementation**

- Create `archon/control/skills.py` with built-in skill definitions.
- Extend config to allow optional skill overrides later, but keep built-ins in code first.
- Teach policy/profile resolution to carry selected skill metadata.
- Keep prompt changes minimal: append skill guidance only when a non-default skill is selected.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py tests/test_agent.py -q -k "skill or profile"`
Expected: PASS.

**Step 5: Commit**

```bash
git add archon/control/skills.py archon/config.py archon/control/policy.py archon/prompt.py tests/test_config.py tests/test_agent.py
git commit -m "feat: add built-in skill registry and skill-backed profiles"
```

### Task 4: Normalize Workers And Calls Into A Shared Job View

**Files:**
- Create: `archon/control/jobs.py`
- Modify: `archon/workers/session_store.py`
- Modify: `archon/calls/store.py`
- Modify: `archon/tooling/worker_session_query_tools.py`
- Modify: `archon/tooling/call_mission_tools.py`
- Test: `tests/test_tools_workers.py`
- Test: `tests/test_tools_calls_missions.py`

**Step 1: Write the failing test**

Add tests that prove:
- a worker session can be rendered as a normalized job summary
- a call mission can be rendered as a normalized job summary
- both include `job_id`, `kind`, `status`, `summary`, and `last_update_at`

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_tools_workers.py tests/test_tools_calls_missions.py -q -k "job summary"`
Expected: FAIL because shared job normalization does not exist.

**Step 3: Write minimal implementation**

- Create `archon/control/jobs.py` with normalization helpers only.
- Do not migrate storage yet.
- Use adapters over existing worker/call stores.
- Expose normalized job summaries in query tools without breaking current output contracts.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_tools_workers.py tests/test_tools_calls_missions.py -q -k "job summary"`
Expected: PASS.

**Step 5: Commit**

```bash
git add archon/control/jobs.py archon/workers/session_store.py archon/calls/store.py archon/tooling/worker_session_query_tools.py archon/tooling/call_mission_tools.py tests/test_tools_workers.py tests/test_tools_calls_missions.py
git commit -m "feat: add shared job normalization for workers and calls"
```

### Task 5: Add Cross-Surface Job Status And Resume Commands

**Files:**
- Modify: `archon/cli_repl_commands.py`
- Modify: `archon/cli_interactive_commands.py`
- Modify: `archon/adapters/telegram.py`
- Modify: `archon/tooling/worker_session_action_tools.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_telegram_adapter.py`

**Step 1: Write the failing test**

Add tests for:
- terminal `/jobs` status listing
- terminal `/job <id>` summary lookup
- Telegram job-status command or callback rendering normalized job output

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py tests/test_telegram_adapter.py -q -k "job"`
Expected: FAIL because cross-surface job UX does not exist.

**Step 3: Write minimal implementation**

- Add lightweight terminal commands for job listing and lookup.
- Add Telegram rendering for the same normalized job summaries.
- Reuse shared job normalization from Task 4.
- Keep resume behavior thin: route only to the existing worker/call continuation paths.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py tests/test_telegram.py -q -k "job"`
Expected: PASS.

**Step 5: Commit**

```bash
git add archon/cli_repl_commands.py archon/cli_interactive_commands.py archon/adapters/telegram.py archon/tooling/worker_session_action_tools.py tests/test_cli.py tests/test_telegram_adapter.py
git commit -m "feat: add cross-surface job status and resume UX"
```

### Task 6: Add Layered Memory Metadata And Task Compaction

**Files:**
- Modify: `archon/memory.py`
- Modify: `archon/agent.py`
- Modify: `archon/tooling/memory_tools.py`
- Test: `tests/test_memory.py`
- Test: `tests/test_agent.py`

**Step 1: Write the failing test**

Add tests that prove:
- memory entries can carry a layer (`session`, `task`, `project`, `user`, `machine`)
- old conversation state can be compacted into a task/session summary artifact
- agent history trimming prefers compaction summary injection over raw turn retention

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_memory.py tests/test_agent.py -q -k "layer or compact"`
Expected: FAIL because layered memory/compaction does not exist.

**Step 3: Write minimal implementation**

- Extend memory metadata/indexing to carry layer information.
- Add a compact summary artifact path for session/task state.
- Keep storage markdown-first; do not add a database.
- Update agent retrieval to prefer compacted summaries when present.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_memory.py tests/test_agent.py -q -k "layer or compact"`
Expected: PASS.

**Step 5: Commit**

```bash
git add archon/memory.py archon/agent.py archon/tooling/memory_tools.py tests/test_memory.py tests/test_agent.py
git commit -m "feat: add layered memory metadata and session compaction"
```

### Task 7: Improve Terminal And Telegram Route-State UX

**Files:**
- Modify: `archon/cli_ui.py`
- Modify: `archon/cli_interactive_commands.py`
- Modify: `archon/adapters/telegram.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_telegram_adapter.py`

**Step 1: Write the failing test**

Add tests that prove:
- terminal displays route/job state for non-fast-lane tasks
- Telegram displays concise progress summaries for job-lane tasks
- token/session summaries include route-aware information where available

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py tests/test_telegram_adapter.py -q -k "route state or progress"`
Expected: FAIL because route-aware UX is not wired.

**Step 3: Write minimal implementation**

- Add route/lane labels to terminal progress UI.
- Add concise Telegram job progress/status rendering.
- Keep display-only changes separate from routing logic.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py tests/test_telegram.py -q -k "route state or progress"`
Expected: PASS.

**Step 5: Commit**

```bash
git add archon/cli_ui.py archon/cli_interactive_commands.py archon/adapters/telegram.py tests/test_cli.py tests/test_telegram_adapter.py
git commit -m "feat: add route-aware terminal and telegram job UX"
```

### Task 8: Add Scoped Read-Only MCP Client Foundations

**Files:**
- Create: `archon/mcp/__init__.py`
- Create: `archon/mcp/client.py`
- Modify: `archon/config.py`
- Modify: `archon/control/policy.py`
- Test: `tests/test_config.py`
- Create: `tests/test_mcp.py`

**Step 1: Write the failing test**

Add tests that prove:
- MCP server definitions can be configured in read-only mode
- policy can deny MCP use by profile
- client-layer output is capped before returning to prompt history

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py tests/test_mcp.py -q`
Expected: FAIL because generic MCP client foundations do not exist.

**Step 3: Write minimal implementation**

- Add `archon/mcp/client.py` for read-only server registration and invocation scaffolding.
- Extend config with explicit MCP server definitions.
- Gate access through policy; do not auto-enable any server.
- Return summarized/capped outputs only.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py tests/test_mcp.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add archon/mcp/__init__.py archon/mcp/client.py archon/config.py archon/control/policy.py tests/test_config.py tests/test_mcp.py
git commit -m "feat: add scoped read-only mcp client foundations"
```

### Task 9: Full Regression, Docs, And Context Sync

**Files:**
- Modify: `docs/plans/2026-03-05-super-assistant-design.md`
- Modify: `AGENT_CONTEXT.json`

**Step 1: Run targeted regression suite**

Run:
```bash
pytest tests/test_agent.py tests/test_cli.py tests/test_config.py tests/test_memory.py tests/test_tools_workers.py tests/test_tools_calls_missions.py -q
```
Expected: PASS.

**Step 2: Run full core suite**

Run:
```bash
python -m pytest tests -q
```
Expected: PASS, or document any environment-specific failures precisely.

**Step 3: Update design and context**

- Sync the design doc with implementation status.
- Append AGENT context entries for route lanes, skills, jobs, memory layering, and MCP status.

**Step 4: Commit**

```bash
git add docs/plans/2026-03-05-super-assistant-design.md AGENT_CONTEXT.json
git commit -m "docs: sync super assistant design and implementation status"
```
