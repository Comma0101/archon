# Assistant UX Clarity Design

## Goal
Make Archon feel closer to Claude Code in daily use: simple to operate, explicit about what it is doing, and powerful without exposing every internal subsystem. The visible UX should stay compact while skills, plugins, MCP, jobs, and Google Deep Research remain available behind a clean control surface.

## Problem Summary
Archon's core is now strong, but the shell experience is still harder to trust than it should be.

Current problems:
- terminal output can be corrupted by concurrent `stderr` writes from tool traces, spinners, and Telegram activity
- bare `/` opens a picker but does not echo the selected command, which makes transcripts ambiguous
- command/help/completion surfaces still contain fake MCP examples like `docs`, even when the live config only has `exa`
- skills, plugins, and MCP usage are not explicit enough in-session, so the user cannot easily tell what Archon actually chose
- assistant explanations about skills/plugins/MCP can drift away from the real registries/configuration
- raw tool output can expose secrets in terminal logs and model history
- Telegram activity is important, but the current terminal experience is not designed to show it cleanly
- broad research requests do not yet have a first-class deep-research execution path

## Design Principles
1. One simple shell surface. Users should not need to understand internal distinctions like tool vs plugin vs MCP vs worker to get value.
2. Explicit state. If Archon activates a skill, chooses MCP, or starts deep research, it must say so clearly.
3. Live configuration over demo defaults. Visible examples must come from actual runtime/config state.
4. Compact eventing. The terminal should show cross-surface activity as short, useful notices rather than raw tool spam.
5. Readline-safe rendering. Background events must never corrupt the user's current terminal input.
6. Truthfulness over fluency. Delivery, verification, and capability claims must be backed by actual state.
7. Lightweight by default. No always-on dashboard, no broad persistent daemons, and no new heavyweight runtime abstractions unless they materially reduce confusion.

## Recommended Approach
Use a professional shell layer over the current assistant kernel.

What stays the same:
- the existing Agent loop remains the default fast path
- workers, calls, MCP, and Telegram remain separate execution systems under the hood
- the router still decides whether a turn should stay fast, become operator work, or become a job

What changes:
- a shared activity/event layer becomes the single source for terminal-visible cross-surface notices
- visible command/help/completion surfaces become runtime-backed instead of static-example-backed
- skills/MCP/plugins become explicit session state with confirmation messages
- Google Deep Research is added as a native background research job, not an MCP server and not a normal chat tool call

## User-Facing UX Model
### Primary shell surface
Keep the visible surface small and consistent:
- `/help`
- `/status`
- `/approvals`
- `/jobs`
- `/mcp`
- `/skills`
- `/reset`

Secondary commands can remain available, but the shell should bias toward these primary entry points.

### `/status`
`/status` becomes the main answer to "what state am I in right now?"

It should summarize:
- model/provider
- active profile
- active skill
- calls state
- MCP/plugin counts
- token usage
- pending approvals
- active job count
- optional last route or last external action if available cheaply

### Bare slash behavior
Typing `/` should still open the command picker, but after selection Archon should echo the picked command back into the prompt before executing it. This preserves the premium picker flow while fixing transcript ambiguity.

### Skills behavior
Natural language requests like `use researcher skill`, `switch to coder mode`, or `act as sales` should auto-activate the matching built-in skill when confidence is high.

Rules:
- high-confidence match: auto-activate and print a short confirmation
- low-confidence match: do not auto-switch, suggest `/skills`
- every auto-activation must be visible in the terminal and persisted only at the current session scope unless the user explicitly asks otherwise

Example notice:
- `[skill] auto-activated: researcher`

### MCP and plugin behavior
Users should not have to memorize fake examples or infer hidden capability selection.

Rules:
- `/mcp`, `/plugins`, and slash completion must only show live configured names
- assistant answers about MCP/plugin availability must be built from live runtime/config data
- if Archon chooses a configured MCP server for work, it should emit a compact notice

Example notice:
- `[mcp] using server: exa`

### Telegram visibility in terminal
Telegram should be visible in the terminal because it is a first-class surface, but the mirrored output should stay compact.

Terminal should show event notices such as:
- `[telegram] message received from 929244301`
- `[telegram] route=job | deep research started`
- `[telegram] approval blocked`
- `[telegram] replied`

It should not mirror every tool call/result from Telegram into the terminal by default.

### Prompt restore behavior
When a Telegram event arrives while the user is typing:
- print the notice above the current line
- restore the prompt
- restore the partially typed input

This is required to make cross-surface activity feel professional instead of intrusive.

## Shared Activity/Event Layer
Introduce a small internal event model rather than ad hoc `stderr` printing from multiple places.

Proposed event categories:
- `shell.command.selected`
- `skill.activated`
- `plugin.used`
- `mcp.used`
- `approval.blocked`
- `approval.approved`
- `job.started`
- `job.updated`
- `job.completed`
- `telegram.received`
- `telegram.replied`
- `research.started`
- `research.completed`

Responsibilities:
- producers emit structured events with minimal payloads
- terminal renderer formats compact notices and restores readline state
- Telegram adapter can keep its own chat-facing behavior while also emitting terminal-visible activity events

This should stay intentionally lightweight. It is not a full trace database or TUI system.

## Truthfulness and Redaction Rules
### Capability truthfulness
Assistant claims about skills, plugins, MCP servers, and enabled features must come from live registries/configuration, not hand-written prose.

### Delivery truthfulness
Claims like "sent to Telegram" must only be made after a confirmed send path succeeds.

### Verification language
Terms like `verified`, `confirmed`, `sent`, and `configured` should be reserved for cases where the system has concrete evidence.

### Secret redaction
Tool results must be redacted before:
- terminal rendering
- Telegram-mirrored terminal notices
- appending tool results back into model history

This is a P0 requirement for the slice because the current tool logging path can surface live secrets.

## Google Deep Research Integration
### Why it belongs in Archon
Broad research tasks are different from ordinary chat. They are long-running, source-heavy, and should behave like a managed background job. Google now provides a real Deep Research agent through the Gemini Interactions API, which fits Archon's job lane well.

### Official constraints
From the official Gemini docs:
- Deep Research is available through the Interactions API, not `generate_content`
- it must run asynchronously with `background=true`
- agent execution with `background=true` requires `store=true`
- it supports follow-up via `previous_interaction_id`
- it is in preview/beta
- it cannot currently use custom Function Calling tools or remote MCP servers
- it includes built-in web tools by default and can additionally use `file_search`
- maximum research time is 60 minutes, with most tasks expected to finish within 20 minutes

Sources:
- https://ai.google.dev/gemini-api/docs/deep-research
- https://ai.google.dev/gemini-api/docs/interactions
- https://ai.google.dev/api/interactions-api

### Integration model
Deep Research should be added as a native Archon background research backend.

It should not be:
- a normal chat tool call inside the existing fast loop
- an MCP server
- a generic worker pretending to be a coding backend

Instead:
- broad research asks can route to `job`
- Archon starts a Deep Research interaction in the background
- Archon stores a normalized research job summary alongside workers/calls
- terminal and Telegram both get compact `research started` / `research completed` notices
- final results are persisted as a report artifact and summarized back into chat

### Routing guidance
Use Deep Research for work like:
- market analysis
- competitive landscaping
- due diligence
- literature review
- broad source synthesis across many URLs/documents

Do not route simple web lookup or bounded fact questions to Deep Research.

### Follow-up behavior
If the user asks a follow-up on a completed Deep Research report, Archon can continue using `previous_interaction_id` rather than restart the job from scratch, as long as the stored interaction id is still available.

## Lightweight Boundaries
To keep Archon lightweight:
- do not add a full TUI/dashboard
- do not mirror full Telegram tool logs into terminal
- do not make Deep Research a permanent resident process
- do not auto-enable every plugin/MCP server on startup
- do not add a second large orchestration framework on top of the current control plane

The new work should be mostly:
- better rendering
- better state exposure
- one small shared event layer
- one native async research integration

## Testing Strategy
1. CLI tests
- bare slash echoes chosen command
- dynamic MCP/plugin completion uses live config names only
- skill auto-activation emits explicit confirmation
- terminal event notices restore prompt/input correctly
- redaction removes obvious secrets from rendered output and history payloads

2. Telegram adapter tests
- Telegram events emit compact terminal notices
- notices do not require raw tool transcript mirroring
- approval and reply events are mirrored cleanly

3. Research job tests
- Deep Research requests create normalized jobs
- background polling updates job status correctly
- final report path/summary appears in `/jobs` and final response
- disabled/unconfigured Deep Research degrades cleanly

4. Router tests
- broad research asks prefer job lane when Deep Research is enabled
- simple research asks remain on the fast path

## Rollout
Phase 1:
- truthfulness cleanup
- live config-backed skills/plugins/MCP shell surfaces
- terminal event renderer and prompt restore
- Telegram compact terminal mirroring
- redaction in tool logging/history

Phase 2:
- Google Deep Research config + backend client
- normalized research job persistence and `/jobs` integration
- routing rules for broad research tasks

Phase 3:
- optional follow-up support using stored Deep Research interaction ids
- refined summaries and artifact handling

## Success Criteria
This slice is successful when:
- Archon feels simpler to use despite having more capabilities
- terminal input is no longer corrupted by background activity
- users can tell which skill/MCP/plugin Archon is actually using
- command surfaces no longer expose fake runtime examples
- Telegram activity appears in terminal as compact professional notices
- broad research tasks can be offloaded to a real Deep Research job path
- no secret-like values appear in terminal or model history from tool results
