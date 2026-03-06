# MCP Runtime Inspection Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add the first safe MCP runtime slice to Archon with terminal inspection commands and an agent-facing read-only MCP tool.

**Architecture:** Extend the existing `archon.mcp.client.MCPClient` into a minimal per-call `stdio` JSON-RPC client that supports `initialize`, `tools/list`, and `tools/call`. Expose MCP to the user through `/mcp` terminal commands and to the agent through one built-in `mcp_call` tool guarded by the existing policy/profile system.

**Tech Stack:** Python 3.11, pytest, existing Archon CLI/tool registry/policy system, stdlib `subprocess` and `json`.

---

### Task 1: Add Failing MCP Runtime Tests

**Files:**
- Modify: `tests/test_mcp.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_agent.py`

**Step 1: Write the failing tests**

Add tests that prove:

- `MCPClient.list_tools()` can parse a successful `tools/list` response through an injected transport adapter
- `mcp_call` is a registered tool and can return a successful read-only MCP tool result
- `/mcp servers` renders configured server state
- `/mcp tools docs` renders advertised MCP tool names/descriptions

**Step 2: Run tests to verify they fail**

Run:

```bash
python -m pytest tests/test_mcp.py tests/test_cli.py tests/test_agent.py -q -k "mcp"
```

Expected: FAIL because runtime listing, terminal commands, and `mcp_call` do not exist.

### Task 2: Implement Minimal MCP Runtime And Agent Tool

**Files:**
- Modify: `archon/mcp/client.py`
- Modify: `archon/tools.py`
- Create: `archon/tooling/mcp_tools.py`
- Modify: `archon/tooling/__init__.py`
- Modify: `archon/control/policy.py`

**Step 1: Write minimal implementation**

- extend `MCPClient` with:
  - `list_tools(server_name, transport_fn=...)`
  - `call_tool(server_name, tool_name, arguments, transport_fn=...)`
  - small shared validation/helpers
- keep `invoke()` as compatibility wrapper or remove it if no longer needed
- add `register_mcp_tools()` with one built-in `mcp_call` tool
- wire `register_mcp_tools()` into `ToolRegistry._register_builtins()`
- enforce `evaluate_mcp_policy()` inside `mcp_call`

**Step 2: Run targeted tests**

Run:

```bash
python -m pytest tests/test_mcp.py tests/test_agent.py -q -k "mcp"
```

Expected: PASS.

### Task 3: Add Terminal MCP Inspection Commands

**Files:**
- Modify: `archon/cli_commands.py`
- Modify: `archon/cli_repl_commands.py`
- Modify: `archon/cli_interactive_commands.py`
- Modify: `archon/cli.py`
- Modify: `tests/test_cli.py`

**Step 1: Write minimal implementation**

- add `/mcp` to slash command metadata and completion
- implement:
  - `/mcp`
  - `/mcp servers`
  - `/mcp tools <server>`
- use `load_config()` and `MCPClient.list_tools()`
- keep output concise and line-oriented

**Step 2: Run targeted tests**

Run:

```bash
python -m pytest tests/test_cli.py -q -k "mcp"
```

Expected: PASS.

### Task 4: Add Real `stdio` Transport And Verify End-To-End Slice

**Files:**
- Modify: `archon/mcp/client.py`
- Modify: `tests/test_mcp.py`
- Modify: `tests/test_config.py`
- Modify: `AGENT_CONTEXT.json`

**Step 1: Add failing transport test**

Add tests that prove:

- the stdio transport sends `initialize` before `tools/list` or `tools/call`
- unsupported transport returns a clear error

**Step 2: Run test to verify it fails**

Run:

```bash
python -m pytest tests/test_mcp.py -q -k "stdio or transport"
```

Expected: FAIL because real stdio transport lifecycle does not exist.

**Step 3: Write minimal implementation**

- add a default stdio JSON-RPC transport in `archon/mcp/client.py`
- keep subprocess interaction injectable for tests
- support only configured `transport="stdio"`
- return short string-safe errors on lifecycle failures

**Step 4: Run verification**

Run:

```bash
python -m pytest tests/test_mcp.py tests/test_config.py tests/test_cli.py tests/test_agent.py -q -k "mcp"
python -m pytest tests -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add archon/mcp/client.py archon/tools.py archon/tooling/mcp_tools.py archon/tooling/__init__.py archon/control/policy.py archon/cli_commands.py archon/cli_repl_commands.py archon/cli_interactive_commands.py archon/cli.py tests/test_mcp.py tests/test_config.py tests/test_cli.py tests/test_agent.py AGENT_CONTEXT.json docs/plans/2026-03-06-mcp-runtime-inspection-design.md docs/plans/2026-03-06-mcp-runtime-inspection-plan.md
git commit -m "feat: add initial mcp runtime and terminal inspection"
```
