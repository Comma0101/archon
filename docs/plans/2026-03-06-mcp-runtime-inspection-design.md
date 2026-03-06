# MCP Runtime Inspection Design

## Goal

Add the first real MCP runtime slice to Archon without turning MCP into a second uncontrolled tool system.

This slice should let:

- the agent call allowed read-only MCP tools through the normal tool registry path
- terminal users inspect configured MCP servers and advertised MCP tools

## Scope

Included:

- `stdio` transport only
- per-call MCP process lifecycle
- `initialize`
- `tools/list`
- `tools/call`
- terminal commands:
  - `/mcp`
  - `/mcp servers`
  - `/mcp tools <server>`
- one agent-facing built-in tool:
  - `mcp_call`

Excluded:

- persistent MCP sessions
- prompts/resources support
- write-capable MCP servers
- Telegram MCP UX
- dynamic MCP server discovery

## Why This Shape

Archon already has:

- config-backed MCP server definitions
- policy evaluation for `mcp:<server>`
- a minimal `MCPClient`
- a single tool registry and hook path

The missing part is safe runtime execution. The correct first step is to plug MCP into the existing tool boundary instead of creating a parallel agent/runtime layer.

## Architecture

### Runtime

Add a minimal `stdio` client adapter under `archon/mcp/client.py`:

- spawn the configured command
- speak JSON-RPC over stdio
- initialize once per invocation
- list tools or call one tool
- terminate the process after the operation

This keeps failure modes narrow and avoids long-lived MCP session state.

### Tooling

Register a new built-in tool:

- `mcp_call(server, tool, arguments={})`

Behavior:

- verify profile policy with `evaluate_mcp_policy`
- reject disabled or non-read-only servers
- call the MCP client
- cap output before it reaches history

The agent still sees one normal Archon tool call, not a second tool protocol.

### Terminal UX

Add terminal inspection commands:

- `/mcp`
  - shows enabled configured servers and usage
- `/mcp servers`
  - lists configured servers with `enabled`, `mode`, `transport`
- `/mcp tools <server>`
  - calls MCP `tools/list` and renders the advertised tool names/descriptions

This gives the user visibility into what MCP can do before enabling agent use.

## Policy Model

Two checks remain in force:

1. config gate
   - server must exist
   - server must be enabled
   - server must be `read_only`
2. profile gate
   - active profile must allow `mcp` or `mcp:<server>`

Terminal inspection should use the same config constraints. Tool execution should use both config and profile constraints.

## Error Handling

Return short explicit errors for:

- missing server
- disabled server
- non-stdio transport
- malformed MCP response
- process launch failure
- initialize failure
- `tools/list` unsupported
- `tools/call` failure
- policy deny

All errors should stay string-safe so they flow cleanly through the existing tool/result UX.

## Testing

Use TDD and keep transport injectable.

Tests should cover:

- terminal `/mcp servers` formatting
- terminal `/mcp tools <server>` formatting
- `mcp_call` registration and success path
- policy deny on `mcp_call`
- `stdio` client request/response handling through an injected subprocess adapter

## Tradeoffs

Pros:

- safe
- lightweight
- easy to debug
- reuses existing policy/tool path

Cons:

- one process spawn per MCP action
- no capability cache
- no prompts/resources yet

That tradeoff is correct for the first slice.
