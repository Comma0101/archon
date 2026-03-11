# Judgment Tool Scope Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make Archon's model-visible tool schema match the active profile/skill scope while preserving runtime policy enforcement.

**Architecture:** Add a narrow filtered-schema API to `ToolRegistry`, route agent turns through profile-aware schema selection, and keep execution-time policy denies as the backstop. This is a judgment/control-plane slice, not a hybrid-runtime rewrite.

**Tech Stack:** Python 3.11+, pytest, existing Archon control/policy/agent stack

---

### Task 1: Add Failing Tests for Visible Tool Scope

**Files:**
- Modify: `tests/test_agent.py`
- Modify: `tests/test_config.py`
- Modify or Create: `tests/test_tools_registry_filesystem.py`

**Step 1: Write the failing tests**

Add tests that prove:
- unrestricted profiles still expose the full tool schema
- constrained profiles expose only allowed tools
- MCP-related permissions preserve or hide `mcp_call` correctly
- a skill-backed profile changes the schema seen by the LLM

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_agent.py tests/test_config.py tests/test_tools_registry_filesystem.py -k 'schema or skill or mcp or visible'`
Expected: FAIL because the agent still passes full schemas through `self.tools.get_schemas()`.

**Step 3: Commit**

```bash
git add tests/test_agent.py tests/test_config.py tests/test_tools_registry_filesystem.py
git commit -m "test: cover profile-aware tool schema visibility"
```

### Task 2: Implement Profile-Aware Tool Schema Filtering

**Files:**
- Modify: `archon/tools.py`
- Modify: `archon/control/policy.py`
- Modify if needed: `archon/control/skills.py`
- Test: `tests/test_tools_registry_filesystem.py`

**Step 1: Write the minimal implementation**

Add a filtered-schema path to `ToolRegistry`.

Implementation requirements:
- add a method like `get_schemas_for_profile(config, profile_name)` or equivalent
- use resolved profile semantics, not a second unrelated ruleset
- return all schemas for `*`
- omit tools not present in the registry without error
- treat `mcp_call` consistently with MCP policy rules

**Step 2: Run tests to verify it passes**

Run: `pytest -q tests/test_tools_registry_filesystem.py tests/test_config.py -k 'schema or mcp or skill'`
Expected: PASS

**Step 3: Commit**

```bash
git add archon/tools.py archon/control/policy.py archon/control/skills.py tests/test_tools_registry_filesystem.py tests/test_config.py
git commit -m "feat: add profile-aware tool schema filtering"
```

### Task 3: Wire Filtered Schemas Into the Agent Loop

**Files:**
- Modify: `archon/agent.py`
- Modify if needed: `archon/prompt.py`
- Test: `tests/test_agent.py`

**Step 1: Write the minimal implementation**

Update `Agent.run()` and `Agent.run_stream()` so they obtain the schema list for the resolved active profile and pass that filtered list into the LLM call path.

Implementation requirements:
- do not change direct native-capability shortcuts like news handling
- keep default behavior unchanged for unrestricted profiles
- ensure `system_prompt` base assembly does not become stale or misleading

**Step 2: Run tests to verify it passes**

Run: `pytest -q tests/test_agent.py -k 'schema or skill or policy or native_news'`
Expected: PASS

**Step 3: Commit**

```bash
git add archon/agent.py archon/prompt.py tests/test_agent.py
git commit -m "feat: scope agent tool schemas by active profile"
```

### Task 4: Verify Runtime Enforcement Still Works

**Files:**
- Modify if needed: `tests/test_agent.py`
- Modify if needed: `tests/test_mcp.py`
- Modify if needed: `tests/test_cli.py`

**Step 1: Add targeted backstop tests**

Add or refine tests proving:
- execution-time policy still denies disallowed tools
- MCP policy still denies blocked servers even if `mcp_call` exists in a schema path
- CLI/runtime surfaces remain truthful about effective scope

**Step 2: Run the focused verification**

Run: `pytest -q tests/test_agent.py tests/test_mcp.py tests/test_cli.py -k 'policy or mcp or permissions or skill'`
Expected: PASS

**Step 3: Commit**

```bash
git add tests/test_agent.py tests/test_mcp.py tests/test_cli.py
git commit -m "test: verify filtered schema and runtime policy stay aligned"
```

### Task 5: Final Verification and Context Sync

**Files:**
- Modify: `CODEBASE_CONTEXT.json`
- Modify: `AGENT_CONTEXT.json`
- Modify if needed: `docs/plans/2026-03-11-judgment-tool-scope-design.md`
- Modify if needed: `docs/plans/2026-03-11-judgment-tool-scope-plan.md`

**Step 1: Run focused milestone verification**

Run: `pytest -q tests/test_agent.py tests/test_config.py tests/test_mcp.py tests/test_cli.py tests/test_tools_registry_filesystem.py`
Expected: PASS

**Step 2: Run the broader suite**

Run: `pytest -q tests`
Expected: PASS

**Step 3: Sync context**

Update the context JSON files with:
- final verified test count
- changelog entry for this slice
- any implementation drift from the design/plan

**Step 4: Commit**

```bash
git add CODEBASE_CONTEXT.json AGENT_CONTEXT.json docs/plans/2026-03-11-judgment-tool-scope-design.md docs/plans/2026-03-11-judgment-tool-scope-plan.md
git commit -m "docs: sync judgment tool scope milestone context"
```
