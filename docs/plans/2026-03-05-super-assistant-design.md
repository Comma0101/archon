# Archon Super Assistant Design

## Implementation Status

As of `2026-03-06`, the current worktree has implemented the first eight plan tasks:

- lane-aware route metadata and a deterministic three-lane classifier
- built-in skill-backed profiles
- shared worker/call job summaries plus `/jobs` and `/job` UX
- layered memory metadata with session/task compaction
- route-aware terminal and Telegram progress UX
- scoped read-only MCP config, policy, and client foundations

Current verification on this worktree:

- targeted regression suite: `175 passed`
- full test suite: `354 passed`

Notable follow-up fix completed during the final regression pass:

- explicit `new session` delegate requests now force the background-session path instead of falling back to `oneshot`, which resolved the old worker regression around sticky-session bypass

## Decision

Evolve Archon into a `two-surface`, `local-first`, `lightweight assistant kernel` where terminal and Telegram are equal first-class interfaces over one shared controller.

Archon should not become a heavy always-on multiagent graph. It should keep the current direct single-agent path for simple requests, and invoke specialist routing only when the task actually needs memory lookup, long-running execution, delegation, calls, or external integrations.

## Why This Shape

Current Archon already has strong primitives:
- A stable core loop and prompt/history guardrails in `archon/agent.py`
- A compatibility-first control/execution split in `archon/control/*` and `archon/execution/*`
- Durable delegated worker sessions in `archon/workers/*` and `archon/tooling/worker_*`
- A lightweight persistent memory system with indexing and inbox review flow in `archon/memory.py`
- Equal user-facing value from terminal and Telegram surfaces via `archon/cli_*` and `archon/adapters/telegram.py`

What Archon lacks is not “more tools.” It lacks a real control plane that can classify work, choose the right lane, preserve context economically, and expose the same behavior across both surfaces.

Official patterns from OpenAI Agents, Claude Code, and the Model Context Protocol all point in the same direction:
- Use specialized roles only when needed
- Keep contexts isolated instead of sharing one giant transcript
- Treat memory, routing, approvals, and external tools as first-class control-plane concerns
- Keep MCP scoped, explicit, and policy-gated

## Goals

1. Make terminal and Telegram equal first-class assistant surfaces.
2. Preserve Archon’s lightweight core for small requests.
3. Add a real router that classifies tasks into direct, operational, or background execution.
4. Promote skills, jobs, and memory into first-class shared system concepts.
5. Improve token economy by preventing large tool and job outputs from bloating the main prompt history.
6. Add MCP later without turning Archon into an uncontrolled integration hub.

## Non-Goals

1. Replace the current `Agent` loop with a full agent graph runtime.
2. Make every request go through a heavy planning phase.
3. Add generic MCP write-capable integrations before routing and policy are mature.
4. Rewrite worker backends (`codex`, `claude_code`, `opencode`) or Telegram transport from scratch.
5. Introduce a vector database or distributed infrastructure by default.

## Product Shape

Archon should become a `two-surface assistant kernel`:

- `Surface adapters`
  - Terminal REPL
  - Telegram chat and voice
- `Control plane`
  - Turn classification
  - Skill selection
  - Memory retrieval and compaction
  - Policy enforcement
  - Job dispatch and approvals
- `Execution plane`
  - Filesystem/content/web/news tools
  - Worker runtime
  - Call runtime
  - Future MCP client layer
- `Shared state`
  - Memory
  - Session state
  - Jobs
  - Approvals
  - Artifacts

Terminal and Telegram should not own behavior decisions. They should render the output of shared controller decisions with surface-specific UX.

## Routing Model

Archon should use a `three-lane router`, not a general-purpose agent graph.

### 1) Fast Lane

Default path for:
- normal chat
- small file reads
- profile/status questions
- light memory lookups
- simple surface commands

Behavior:
- stays close to the current `Agent.run()` path
- lowest latency
- lowest token cost
- no background job creation unless the task is reclassified

### 2) Operator Lane

For bounded actions with clear scope:
- file operations
- command execution
- memory inbox actions
- worker status queries
- news/call control actions
- targeted web retrieval

Behavior:
- still synchronous from the user’s point of view
- routed through policy, hooks, and better intent classification
- can escalate into a job if the task grows beyond a bounded scope

### 3) Job Lane

For broad or long-running tasks:
- deep repo review
- multi-step coding
- long web research
- lead generation
- outbound call missions
- multi-turn delegated work

Behavior:
- creates a tracked job/session record
- dispatches to worker runtime, call runtime, or future research runtime
- returns summaries and artifact references instead of feeding raw logs back into the main prompt

This routing model keeps simple requests cheap while giving complex requests a durable execution path.

## Skill Model

Skills should become first-class Archon routing profiles, not always-running separate agents.

Each skill should define:
- system prompt pack
- allowed tools
- preferred model/provider
- max execution lane
- result contract
- optional memory scope hints

Initial skill set:
- `general`
- `coder`
- `researcher`
- `operator`
- `sales`
- `memory_curator`

Recommendation:
- keep skills local and declarative
- map them onto policy profiles and routing rules
- do not spawn separate contexts unless the chosen lane or task demands it

## Memory Model

Archon should move from one broad memory bucket to layered memory:

- `session`
  - current conversation state
- `task`
  - active objective, constraints, current artifacts, last useful next step
- `project`
  - repository facts, conventions, architecture notes, known issues
- `user`
  - preferences and durable operating rules
- `machine`
  - local environment facts, installed tools, service endpoints

The existing markdown memory + inbox design in `archon/memory.py` is good enough to extend. The priority is not a storage rewrite. The priority is:
- better retrieval targeting
- memory write discipline
- compaction of long conversations into structured task/session summaries
- better separation between ephemeral and durable facts

## Job Model

Long-running work should be represented as first-class jobs shared by terminal and Telegram.

Every job should expose:
- `job_id`
- `kind`
- `status`
- `surface_origin`
- `skill`
- `summary`
- `artifact_refs`
- `approval_state`
- `last_update_at`
- `resume_hint`

Existing worker sessions are one job type. Call missions are another. Future research crawls can be a third.

This lets either surface ask to continue, summarize, approve, or inspect the same job.

## Telegram And Terminal Parity

Terminal and Telegram are equal in product weight. They should differ in presentation, not capability.

### Terminal strengths
- streaming output
- slash commands and keyboard completion
- dense logs/status views
- interactive approval/review workflows

### Telegram strengths
- async/mobile access
- concise progress updates
- inline approvals
- voice input/output
- background-task notifications

Shared behavior should include:
- same routing model
- same memory retrieval
- same skills/policy model
- same job IDs and approval state
- same durable task summaries

## MCP Strategy

MCP should be added later through a dedicated `archon/mcp/` client layer.

Rules:
- read-only servers first
- explicit capability allowlists by profile
- output caps before prompt history inclusion
- approval gate for side effects
- roots/resources/prompts/tools handled at the controller boundary
- no “all servers attached all the time” default

Current `archon/workers/claude_permission_mcp.py` remains a narrow worker-specific approval bridge. It is not the platform MCP architecture.

## UX Direction

Terminal UX should improve through:
- better slash-command discovery and selection
- clearer lane/job state (`fast`, `operator`, `job`)
- active job list and resume affordances
- clearer approval state
- token accounting split by history, tool results, and job summaries

Telegram UX should improve through:
- concise progress updates for jobs
- inline resume/approve/status actions
- consistent job summaries
- voice parity for status/result delivery where useful

## Architecture Changes By Layer

### Surface layer
- Keep `archon/cli_*` and `archon/adapters/telegram.py`
- Move behavior selection out of surfaces
- Add shared surface event rendering for jobs, approvals, and route state

### Control plane
- Expand `archon/control/orchestrator.py` from compatibility wrapper into a real triage/router
- Extend `archon/control/policy.py` from tool-level gating into skill/lane/backend capability policy
- Use `archon/control/hooks.py` as trace/eval/event plumbing
- Extend `archon/control/session_controller.py` into shared job/session resolution logic

### Execution plane
- Keep `archon/execution/*` as the compatibility boundary
- Reuse worker runtime, call runtime, and existing tools
- Add future `archon/mcp/` execution adapters behind policy and output budgets

### Shared state
- Layer `archon/memory.py`
- Add unified job metadata store shared by workers and call missions
- Persist route/summary artifacts separately from raw tool logs

## Phased Rollout

### Phase A: Real Router Over Existing Core
- Add lane classification without changing default direct behavior
- Keep `fast lane` as the common path
- Emit route decisions via hooks

### Phase B: Skills As Routing Profiles
- Add skill registry/config
- Map skills to tools, models, and lane limits
- Reuse current policy profile machinery where possible

### Phase C: Shared Job Ledger
- Normalize worker sessions and call missions into one job view
- Add cross-surface resume/status/approval flow

### Phase D: Memory Layering And Compaction
- Add session/task/project/user/machine memory layers
- Summarize old turns into task/session state instead of carrying giant transcripts

### Phase E: Surface UX Upgrade
- Terminal job/status palette and better command UX
- Telegram job cards, concise updates, and approval/resume controls

### Phase F: Scoped MCP Client Layer
- Read-only MCP first
- Profile-gated write capabilities later

## Success Criteria

1. Simple terminal and Telegram requests stay fast and cheap.
2. Broad tasks leave the fast lane and become resumable jobs.
3. Terminal and Telegram can inspect and continue the same job.
4. Long-running work returns summaries and artifact refs instead of raw prompt bloat.
5. Memory retrieval becomes more relevant while prompt history shrinks.
6. Skills improve behavior without forcing multiagent overhead on every turn.
7. MCP integrations remain optional and policy-constrained.

## Risks And Controls

### Risk: Overengineering the controller
Control:
- keep only three lanes
- preserve direct fast-path behavior
- add routing incrementally under hybrid mode

### Risk: Token usage stays high
Control:
- isolate long tasks into jobs
- summarize job returns
- compact old session state into task/session summaries

### Risk: Surface divergence between terminal and Telegram
Control:
- one shared controller
- one job model
- one policy/skill model

### Risk: MCP expands blast radius
Control:
- add it only after router/skills/jobs exist
- read-only first
- approval gates and output caps

## Sources

- OpenAI Agents SDK: <https://developers.openai.com/api/docs/guides/agents-sdk>
- OpenAI agent safety guidance: <https://developers.openai.com/api/docs/guides/agent-builder-safety>
- Claude Code subagents: <https://code.claude.com/docs/en/sub-agents>
- Claude Code memory: <https://code.claude.com/docs/en/memory>
- Claude Code MCP: <https://code.claude.com/docs/en/mcp>
- MCP client concepts: <https://modelcontextprotocol.io/docs/learn/client-concepts>
- MCP lifecycle specification: <https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle>
