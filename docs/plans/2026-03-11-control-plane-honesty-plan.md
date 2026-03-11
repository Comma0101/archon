# Control Plane Honesty Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make Archon's control-plane status/help/route surfaces truthful about the current runtime and prefer built-in paths for bounded native requests.

**Architecture:** Keep `legacy` and `hybrid` as the runtime/config names, but centralize honest user-facing descriptors for orchestrator mode and route paths. Tighten bounded native-request detection so AI news, jobs, and research-status requests prefer direct handlers before generic LLM fallthrough. This slice does not change the underlying shared-executor architecture.

**Tech Stack:** Python 3.11+, pytest, existing Archon CLI/control/Telegram stack

---

### Task 1: Add Failing Tests for Control-Plane Honesty

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `tests/test_agent.py`
- Modify if needed: `tests/test_telegram_adapter.py`

**Step 1: Write the failing tests**

Add tests that prove:
- `/status` describes `hybrid` in a way that reflects shared-executor reality
- route payloads and turn stats use truthful shared-executor wording
- explicit bounded native asks such as AI news and job/research status do not fall through to generic chat behavior

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_cli.py tests/test_agent.py tests/test_telegram_adapter.py -k 'hybrid or route or news or jobs or research'`
Expected: FAIL because some current strings and route preferences still reflect older wording or looser fallthrough behavior.

**Step 3: Commit**

```bash
git add tests/test_cli.py tests/test_agent.py tests/test_telegram_adapter.py
git commit -m "test: cover control plane honesty surfaces"
```

### Task 2: Centralize Truthful Orchestrator Descriptors

**Files:**
- Modify: `archon/control/orchestrator.py`
- Modify: `archon/cli_repl_commands.py`
- Modify: `archon/cli_ui.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_agent.py`

**Step 1: Write the minimal implementation**

Add a shared description layer for:
- orchestrator mode labels shown to users
- route path labels emitted in hooks or surfaced in turn stats

Implementation requirements:
- keep config/runtime values stable: `legacy`, `hybrid`
- describe `hybrid` as a shared-executor mode, not a distinct planner runtime
- reuse shared helpers instead of scattering new string literals across CLI and hooks

**Step 2: Run tests to verify it passes**

Run: `pytest -q tests/test_cli.py tests/test_agent.py -k 'hybrid or route or status'`
Expected: PASS

**Step 3: Commit**

```bash
git add archon/control/orchestrator.py archon/cli_repl_commands.py archon/cli_ui.py tests/test_cli.py tests/test_agent.py
git commit -m "fix: make control plane descriptors truthful"
```

### Task 3: Tighten Native Capability Preference for Bounded Requests

**Files:**
- Modify: `archon/control/session_controller.py`
- Modify: `archon/agent.py`
- Modify: `archon/adapters/telegram.py`
- Modify if needed: `archon/cli_repl_commands.py`
- Test: `tests/test_agent.py`
- Test: `tests/test_telegram_adapter.py`

**Step 1: Write the minimal implementation**

Tighten recognition and direct handling for explicit bounded native asks, including:
- AI news requests
- job listing / job show requests
- research status requests

Implementation requirements:
- prefer native handlers only for explicit bounded intents
- preserve general LLM fallthrough for ambiguous or exploratory prompts
- keep terminal and Telegram behavior aligned where they share the same intent surface

**Step 2: Run tests to verify it passes**

Run: `pytest -q tests/test_agent.py tests/test_telegram_adapter.py tests/test_cli.py -k 'news or jobs or research or native or status'`
Expected: PASS

**Step 3: Commit**

```bash
git add archon/control/session_controller.py archon/agent.py archon/adapters/telegram.py archon/cli_repl_commands.py tests/test_agent.py tests/test_telegram_adapter.py tests/test_cli.py
git commit -m "fix: prefer native bounded control-plane flows"
```

### Task 4: Verify Parity and Regressions

**Files:**
- Modify if needed: `tests/test_cli.py`
- Modify if needed: `tests/test_telegram_adapter.py`
- Modify if needed: `tests/test_agent.py`

**Step 1: Add focused parity/backstop tests**

Add or refine tests that prove:
- terminal and Telegram share the same native preference for AI news where intended
- status/help output remains compact and truthful
- route metadata stays stable for both streaming and non-streaming paths

**Step 2: Run focused verification**

Run: `pytest -q tests/test_cli.py tests/test_telegram_adapter.py tests/test_agent.py -k 'parity or route or status or news'`
Expected: PASS

**Step 3: Commit**

```bash
git add tests/test_cli.py tests/test_telegram_adapter.py tests/test_agent.py
git commit -m "test: lock control plane parity and route honesty"
```

### Task 5: Final Verification and Context Sync

**Files:**
- Modify: `CODEBASE_CONTEXT.json`
- Modify: `AGENT_CONTEXT.json`
- Modify if needed: `docs/plans/2026-03-11-control-plane-honesty-design.md`
- Modify if needed: `docs/plans/2026-03-11-control-plane-honesty-plan.md`

**Step 1: Run focused milestone verification**

Run: `pytest -q tests/test_cli.py tests/test_agent.py tests/test_telegram_adapter.py`
Expected: PASS

**Step 2: Run the broader suite**

Run: `pytest -q tests`
Expected: PASS

**Step 3: Sync context**

Update the context JSON files with:
- final verified test count
- changelog entry for this slice
- any design/plan drift discovered during implementation

**Step 4: Commit**

```bash
git add CODEBASE_CONTEXT.json AGENT_CONTEXT.json docs/plans/2026-03-11-control-plane-honesty-design.md docs/plans/2026-03-11-control-plane-honesty-plan.md
git commit -m "docs: sync control plane honesty context"
```
