# Terminal Command Surface Design

## Goal
Make Archon's terminal slash UX feel closer to Claude Code/Codex without turning the shell into a heavy TUI. The command surface should be easier to discover, more internally consistent, and driven by live runtime state instead of partial static lists.

## Problems
- Slash completion is inconsistent across commands. Some subcommands and values complete, others are partial or missing.
- The `/` picker and `Tab` completion do not feel like one system.
- The visible command surface is cluttered by duplicate concepts like `/model-list` vs `/model-set`.
- `/permissions` is inspect-only even though it represents a mode the user should be able to set.
- Commands with live values (`/profile`, `/skills`, `/plugins`, `/mcp`, `/job`) do not consistently use actual runtime state.

## Design Principles
- Keep the shell lightweight and text-first.
- Prefer one command model shared across picker and completer.
- Present executable leaf actions, not abstract verbs, whenever possible.
- Keep backward compatibility for legacy command aliases, but remove them from the primary surface when they duplicate better commands.
- Use live runtime/config/state to build suggestions.

## UX Changes
### 1. Unified slash suggestion model
Build a single runtime-backed suggestion model that feeds:
- `Tab` completion
- bare `/` picker
- filtered picker for partial slash input submitted with `Enter`

This will let `/`, `/m`, `/profile set`, `/skills use`, `/mcp show`, `/job`, and similar flows draw from the same suggestion source.

### 2. Filtered picker on Enter for incomplete slash input
When the user submits an incomplete slash command such as `/m`, `/profile set`, or `/skills use`, Archon should open a filtered picker instead of falling through to unknown-command behavior or requiring exact syntax up front.

This is the lightweight approximation of a command palette: it improves Enter-based selection without requiring a full live overlay TUI.

### 3. Simpler visible command surface
Primary model UX becomes:
- `/model`
- `/model set <provider-model>`

`/model-set` and `/model-list` remain backward-compatible aliases, but are removed from the primary picker/help/completion surface.

### 4. Actionable permissions command
`/permissions` becomes:
- `/permissions`
- `/permissions auto`
- `/permissions accept_reads`
- `/permissions confirm_all`

This is distinct from `/approvals`, which remains session-local dangerous-action approval state.

### 5. Live runtime-backed values
Completion/picker values should come from live state where possible:
- `/profile set <configured-profile>`
- `/skills show|use <built-in-skill>`
- `/plugins show <native-or-mcp-plugin>`
- `/mcp show|tools <configured-server>`
- `/jobs active|all|purge`
- `/job <recent-job-id>`
- `/permissions <mode>`
- `/model set <provider-model>`

## Non-Goals
- No full-screen TUI or persistent dashboard.
- No prompt_toolkit migration.
- No attempt in this slice to create a true live overlay that appears immediately on the first `/` keystroke before submit. That requires a different input architecture.

## Compatibility
- Existing commands should continue to work.
- Hidden aliases can stay for now if they map cleanly:
  - `/model-list`
  - `/model-set <provider-model>`

## Testing Strategy
- Add focused tests for:
  - hidden model aliases vs visible surface
  - `/permissions` mode completion and behavior
  - dynamic profile/skill/plugin/MCP/job suggestion building
  - filtered picker behavior for partial slash input submitted with `Enter`
  - command help surface updates
- Keep full suite green.
