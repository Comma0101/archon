# MCP Env Support And Exa Setup Design

## Goal

Add safe environment-variable support for configured MCP servers, then wire Archon's first real external MCP server using the existing local Exa MCP checkout.

## Scope

Included:

- `env` support in `[mcp.servers.<name>]`
- `${VAR}` interpolation from process environment
- merging configured MCP env into the spawned stdio subprocess
- configuring a local `exa` MCP server in `~/.config/archon/config.toml`
- one real smoke test using Exa

Excluded:

- general `.env` file loading
- secrets vault integration
- persistent MCP sessions
- write-capable MCP servers

## Design

### Config

Extend `MCPServerConfig` with:

- `env: dict[str, str]`

Rules:

- keys are normalized as provided
- values can be literal strings
- values of the form `${NAME}` resolve from `os.environ["NAME"]`
- missing env vars resolve to an empty string

This keeps config simple and avoids inventing another config language.

### Runtime

When Archon launches an MCP stdio process:

- start from `os.environ.copy()`
- overlay resolved `server.env`
- pass the merged env to `subprocess.Popen`

That gives each MCP server a scoped environment without affecting the parent Archon process.

### Exa Setup

Use the existing local checkout:

- command: `node`
- args: `/home/comma/Documents/Cline/MCP/exa-mcp-server/build/index.js`
- tools restriction: `--tools=web_search,research_paper_search,crawling`
- env:
  - `EXA_API_KEY = "${EXA_API_KEY}"`

This keeps the Exa API key out of Archon config while still making the server runnable when the environment is present.

## Validation

Tests should prove:

- MCP env config parses correctly
- `${VAR}` interpolation resolves as expected
- stdio transport passes env through to the child process

Smoke test should prove:

- `/mcp servers` shows `exa`
- `/mcp tools exa` returns tools when `EXA_API_KEY` is present

## Tradeoff

This is intentionally minimal. It solves the real blocker for hosted/read-only MCP servers without dragging in `.env` loaders or secret managers.
