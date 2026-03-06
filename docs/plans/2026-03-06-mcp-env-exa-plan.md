# MCP Env Support And Exa Setup Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add env support for MCP servers and configure a real read-only Exa MCP server for Archon without storing the Exa API key directly in Archon config.

**Architecture:** Extend MCP config parsing to accept per-server `env` maps with `${VAR}` interpolation from the current process environment. Update the MCP stdio transport to merge those resolved env vars into the child process, then configure the local Exa MCP server in user config and validate it with terminal MCP inspection commands.

**Tech Stack:** Python 3.11+, pytest, stdlib `os`/`subprocess`, existing Archon MCP runtime and CLI.

---

### Task 1: Add Failing Tests For MCP Env Parsing And Transport

**Files:**
- Modify: `tests/test_config.py`
- Modify: `tests/test_mcp.py`

**Step 1: Write the failing tests**

Add tests that prove:

- `[mcp.servers.exa].env` parses into `MCPServerConfig.env`
- `${EXA_API_KEY}` interpolates from process environment
- the default stdio transport passes resolved env vars into the child process

**Step 2: Run tests to verify they fail**

Run:

```bash
python -m pytest tests/test_config.py tests/test_mcp.py -q -k "mcp and env"
```

Expected: FAIL because MCP server env support does not exist.

### Task 2: Implement MCP Env Support

**Files:**
- Modify: `archon/config.py`
- Modify: `archon/mcp/client.py`

**Step 1: Write minimal implementation**

- extend `MCPServerConfig` with `env`
- parse `[mcp.servers.<name>.env]` string values
- resolve `${VAR}` placeholders from `os.environ`
- merge resolved env into MCP stdio subprocess launches

**Step 2: Run targeted tests**

Run:

```bash
python -m pytest tests/test_config.py tests/test_mcp.py -q -k "mcp and env"
```

Expected: PASS.

### Task 3: Configure Exa And Smoke Test

**Files:**
- Modify: `~/.config/archon/config.toml`
- Modify: `AGENT_CONTEXT.json`

**Step 1: Add Exa MCP server config**

Configure:

- server name: `exa`
- `enabled = true`
- `mode = "read_only"`
- `transport = "stdio"`
- local Exa MCP entrypoint path
- limited tool allowlist arguments
- `env.EXA_API_KEY = "${EXA_API_KEY}"`

**Step 2: Smoke test**

Run:

```bash
EXA_API_KEY=... archon run "/mcp servers"
EXA_API_KEY=... archon run "/mcp tools exa"
```

Or equivalent direct module/CLI path that exercises the same config/runtime.

Expected: Archon lists the Exa server and advertised tools.

### Task 4: Verify And Commit

**Files:**
- Modify: `AGENT_CONTEXT.json`

**Step 1: Run verification**

Run:

```bash
python -m pytest tests/test_config.py tests/test_mcp.py -q
python -m pytest tests -q
```

Expected: PASS.

**Step 2: Commit**

```bash
git add archon/config.py archon/mcp/client.py tests/test_config.py tests/test_mcp.py AGENT_CONTEXT.json docs/plans/2026-03-06-mcp-env-exa-design.md docs/plans/2026-03-06-mcp-env-exa-plan.md
git commit -m "feat: add mcp env support and exa setup"
```
