# Reliability and Coherence Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Improve Archon's reliability, runtime truthfulness, code cleanliness, and command/voice UX before adding new feature scope.

**Architecture:** Work in four bounded phases: lock down runtime truthfulness, tighten capability routing, remove stale or duplicate paths while regenerating `CODEBASE_CONTEXT.json`, then finish with UX polish that increases user trust. Prefer minimal targeted fixes with regression tests over broad rewrites.

**Tech Stack:** Python, pytest, Archon CLI/Telegram adapters, research/job stores, JSON context artifact generation.

---

### Task 1: Baseline Runtime Truthfulness

**Files:**
- Modify: `archon/cli_repl_commands.py`
- Modify: `archon/adapters/telegram.py`
- Modify: `archon/research/store.py`
- Modify: `archon/workers/runtime.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_telegram_adapter.py`
- Test: `tests/test_research_store.py`
- Test: `tests/test_tools_workers.py`

**Step 1: Write the failing tests**
- Add focused tests for any currently incorrect runtime/status surfaces found during implementation kickoff.
- Cover at least one job-status surface and one cross-surface route/status parity case.

**Step 2: Run tests to verify they fail**
Run: `pytest tests/test_cli.py tests/test_telegram_adapter.py tests/test_research_store.py tests/test_tools_workers.py -q -k 'status or route or parity or job'`
Expected: FAIL for the new targeted regressions.

**Step 3: Write minimal implementation**
- Fix only the incorrect status/route reporting paths required by the failing tests.
- Keep runtime labels conservative and truthful.

**Step 4: Run tests to verify they pass**
Run: `pytest tests/test_cli.py tests/test_telegram_adapter.py tests/test_research_store.py tests/test_tools_workers.py -q -k 'status or route or parity or job'`
Expected: PASS

**Step 5: Commit**
```bash
git add archon/cli_repl_commands.py archon/adapters/telegram.py archon/research/store.py archon/workers/runtime.py tests/test_cli.py tests/test_telegram_adapter.py tests/test_research_store.py tests/test_tools_workers.py
git commit -m "fix: make runtime status surfaces truthful"
```

### Task 2: Tighten Decision Discipline

**Files:**
- Modify: `archon/control/orchestrator.py`
- Modify: `archon/control/session_controller.py`
- Modify: `archon/agent.py`
- Modify: `archon/adapters/telegram.py`
- Modify: `archon/tooling/content_tools.py`
- Test: `tests/test_orchestrator.py`
- Test: `tests/test_telegram_adapter.py`
- Test: `tests/test_agent.py`

**Step 1: Write the failing tests**
- Add tests for native-path preference where generic chat or web search should not win.
- Add tests for one or two high-frequency misroutes only. Do not broaden the scope yet.

**Step 2: Run tests to verify they fail**
Run: `pytest tests/test_orchestrator.py tests/test_telegram_adapter.py tests/test_agent.py -q -k 'news or route or native or command'`
Expected: FAIL for the new routing expectations.

**Step 3: Write minimal implementation**
- Prefer dedicated local capabilities over generic model/tool fallthrough in the targeted cases.
- Keep the routing logic simple and explicit; do not introduce a new model-selection layer here.

**Step 4: Run tests to verify they pass**
Run: `pytest tests/test_orchestrator.py tests/test_telegram_adapter.py tests/test_agent.py -q -k 'news or route or native or command'`
Expected: PASS

**Step 5: Commit**
```bash
git add archon/control/orchestrator.py archon/control/session_controller.py archon/agent.py archon/adapters/telegram.py archon/tooling/content_tools.py tests/test_orchestrator.py tests/test_telegram_adapter.py tests/test_agent.py
git commit -m "fix: tighten capability routing discipline"
```

### Task 3: Remove Stale Paths and Regenerate Context

**Files:**
- Modify: `archon/agent.py`
- Modify: `archon/control/orchestrator.py`
- Modify: `archon/control/skills.py`
- Modify: `archon/cli_commands.py`
- Create or Modify: `CODEBASE_CONTEXT.json`
- Test: `tests/test_agent.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_orchestrator.py`

**Step 1: Identify and write failing tests for stale assumptions**
- Add tests only where a stale branch or legacy assumption is still encoded in behavior.
- If no existing test should fail, add validation coverage for the simplified path instead.

**Step 2: Run tests to verify the targeted coverage fails or is missing**
Run: `pytest tests/test_agent.py tests/test_cli.py tests/test_orchestrator.py -q`
Expected: FAIL on the new targeted cases, or confirm the missing coverage before implementing.

**Step 3: Write minimal implementation**
- Remove or simplify stale code paths that the current runtime no longer needs.
- Regenerate `CODEBASE_CONTEXT.json` so it matches the post-cleanup architecture.
- Keep schema and content deterministic.

**Step 4: Validate the context artifact**
Run: `jq empty CODEBASE_CONTEXT.json`
Expected: PASS with no output

**Step 5: Run tests to verify they pass**
Run: `pytest tests/test_agent.py tests/test_cli.py tests/test_orchestrator.py -q`
Expected: PASS

**Step 6: Commit**
```bash
git add archon/agent.py archon/control/orchestrator.py archon/control/skills.py archon/cli_commands.py CODEBASE_CONTEXT.json tests/test_agent.py tests/test_cli.py tests/test_orchestrator.py
git commit -m "refactor: remove stale control paths and sync codebase context"
```

### Task 4: Finish the Trustable UX Pass

**Files:**
- Modify: `archon/slash_palette.py`
- Modify: `archon/ux/terminal_feed.py`
- Modify: `archon/cli_interactive_commands.py`
- Modify: `archon/adapters/telegram.py`
- Modify: `archon/audio/tts.py`
- Test: `tests/test_slash_palette.py`
- Test: `tests/test_terminal_feed.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_audio_tts.py`
- Test: `tests/test_telegram_adapter.py`

**Step 1: Write the failing tests**
- Add only the UX regressions still open after Tasks 1-3.
- Keep tests behavior-focused: prompt redraw, slash predictability, `/jobs` clarity, and long voice reply completion.

**Step 2: Run tests to verify they fail**
Run: `pytest tests/test_slash_palette.py tests/test_terminal_feed.py tests/test_cli.py tests/test_audio_tts.py tests/test_telegram_adapter.py -q`
Expected: FAIL for the new UX regressions.

**Step 3: Write minimal implementation**
- Fix the specific trust-breaking UX issues without redesigning the entire shell.
- Preserve parity between terminal and Telegram only where parity is intentional.

**Step 4: Run tests to verify they pass**
Run: `pytest tests/test_slash_palette.py tests/test_terminal_feed.py tests/test_cli.py tests/test_audio_tts.py tests/test_telegram_adapter.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add archon/slash_palette.py archon/ux/terminal_feed.py archon/cli_interactive_commands.py archon/adapters/telegram.py archon/audio/tts.py tests/test_slash_palette.py tests/test_terminal_feed.py tests/test_cli.py tests/test_audio_tts.py tests/test_telegram_adapter.py
git commit -m "fix: improve trusted terminal and telegram ux"
```

### Task 5: Verify the Milestone and Sync Final Context

**Files:**
- Modify: `CODEBASE_CONTEXT.json`
- Modify: `docs/plans/2026-03-10-reliability-coherence-design.md`
- Modify: `docs/plans/2026-03-10-reliability-coherence-plan.md`

**Step 1: Run the focused milestone verification**
Run: `pytest tests/test_cli.py tests/test_telegram_adapter.py tests/test_terminal_feed.py tests/test_audio_tts.py tests/test_agent.py tests/test_orchestrator.py tests/test_research_store.py tests/test_tools_workers.py tests/test_slash_palette.py -q`
Expected: PASS

**Step 2: Run the broader suite**
Run: `pytest tests -q`
Expected: PASS

**Step 3: Sync final context**
- Update `CODEBASE_CONTEXT.json` with the final architecture state after this milestone.
- If implementation drifted from the design, update these plan docs so they remain truthful.

**Step 4: Commit**
```bash
git add CODEBASE_CONTEXT.json docs/plans/2026-03-10-reliability-coherence-design.md docs/plans/2026-03-10-reliability-coherence-plan.md
git commit -m "docs: finalize reliability and coherence milestone context"
```
