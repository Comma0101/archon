# Token Usage Ledger Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a persistent token-usage ledger so Archon can report truthful token totals across chat and tracked side paths.

**Architecture:** Keep the existing in-memory chat session counters for lightweight UI continuity, but add a JSONL-backed usage ledger under Archon state as the source of truth for persisted accounting. Wire the recorder into the main turn executor first, then news summarization, and update reporting surfaces to distinguish session-chat totals from workflow totals instead of pretending they are the same number.

**Tech Stack:** Python 3.11+, dataclasses, pathlib, JSONL state files, pytest

---

### Task 1: Add Failing Tests for Usage Ledger Storage

**Files:**
- Create: `tests/test_usage_store.py`
- Create if needed later: `archon/usage/__init__.py`
- Create later: `archon/usage/models.py`
- Create later: `archon/usage/store.py`

**Step 1: Write the failing tests**

Add tests that prove:
- a usage event can be appended to a JSONL ledger
- session summaries total `input_tokens` and `output_tokens` correctly
- source-grouped summaries remain distinct (`chat` vs `news`)
- missing usage data is ignored rather than fabricated

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_usage_store.py`
Expected: FAIL because the usage ledger module does not exist yet.

**Step 3: Commit**

```bash
git add tests/test_usage_store.py
git commit -m "test: add usage ledger storage coverage"
```

### Task 2: Implement Usage Event Model and Store

**Files:**
- Create: `archon/usage/__init__.py`
- Create: `archon/usage/models.py`
- Create: `archon/usage/store.py`
- Modify: `archon/config.py` only if a new state path helper is needed
- Test: `tests/test_usage_store.py`

**Step 1: Write the minimal implementation**

Implement:
- a `UsageEvent` dataclass
- append-only JSONL persistence
- helpers to summarize totals by session and by source
- small path helper(s) under Archon state

Keep the event schema minimal and avoid adding pricing fields.

**Step 2: Run tests to verify it passes**

Run: `pytest -q tests/test_usage_store.py`
Expected: PASS

**Step 3: Commit**

```bash
git add archon/usage/__init__.py archon/usage/models.py archon/usage/store.py tests/test_usage_store.py
git commit -m "feat: add token usage ledger store"
```

### Task 3: Add Failing Tests for Main Chat Usage Recording

**Files:**
- Modify: `tests/test_agent.py`
- Modify if better targeted: `tests/test_cli.py`
- Modify later: `archon/execution/turn_executor.py`
- Modify later: `archon/agent.py`

**Step 1: Write the failing tests**

Add tests that prove:
- non-streaming assistant turns record one usage event when an `LLMResponse` returns usage
- streaming assistant turns also record usage
- existing `total_input_tokens` / `total_output_tokens` still increment as before

Use a temp ledger path or monkeypatched recorder so tests stay isolated.

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_agent.py -k 'usage ledger or workflow tokens'`
Expected: FAIL because the turn executor does not record ledger events yet.

**Step 3: Commit**

```bash
git add tests/test_agent.py
git commit -m "test: cover main chat usage recording"
```

### Task 4: Implement Main Chat Usage Recording

**Files:**
- Modify: `archon/agent.py`
- Modify: `archon/execution/turn_executor.py`
- Modify if needed: `archon/cli_runtime.py`
- Test: `tests/test_agent.py`
- Test if needed: `tests/test_cli.py`

**Step 1: Write the minimal implementation**

Add a small recorder hook so:
- the active agent session records usage events for each LLM response
- both streaming and non-streaming executor paths use the same helper
- current in-memory counters remain unchanged

If needed, add `session_id` ownership on the agent so ledger events can be tied to the live chat session.

**Step 2: Run tests to verify it passes**

Run: `pytest -q tests/test_agent.py -k 'usage ledger or workflow tokens'`
Expected: PASS

**Step 3: Commit**

```bash
git add archon/agent.py archon/execution/turn_executor.py tests/test_agent.py tests/test_cli.py
git commit -m "feat: record main chat token usage"
```

### Task 5: Add Failing Tests for News Usage Recording

**Files:**
- Modify: `tests/test_news_summarize.py`
- Modify later: `archon/news/summarize.py`

**Step 1: Write the failing tests**

Add tests that prove:
- successful LLM-based news summarization records a `news` usage event when usage metadata is present
- fallback digest generation does not fabricate token usage

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_news_summarize.py -k 'usage'`
Expected: FAIL because news summarization does not currently write usage events.

**Step 3: Commit**

```bash
git add tests/test_news_summarize.py
git commit -m "test: cover news token usage recording"
```

### Task 6: Implement News Usage Recording

**Files:**
- Modify: `archon/news/summarize.py`
- Modify if needed: `archon/news/runner.py`
- Test: `tests/test_news_summarize.py`

**Step 1: Write the minimal implementation**

Add an optional recorder path so news summarization can record usage events when the LLM path returns token metadata. Keep fallback/no-usage paths explicit and quiet.

**Step 2: Run tests to verify it passes**

Run: `pytest -q tests/test_news_summarize.py -k 'usage'`
Expected: PASS

**Step 3: Commit**

```bash
git add archon/news/summarize.py archon/news/runner.py tests/test_news_summarize.py
git commit -m "feat: record news token usage"
```

### Task 7: Add Truthful Reporting Tests

**Files:**
- Modify: `tests/test_cli.py`
- Modify later: `archon/cli_repl_commands.py`
- Modify later if needed: `archon/cli_interactive_commands.py`

**Step 1: Write the failing tests**

Add tests that prove:
- `/status` remains lightweight and still reports chat-session tokens only
- `/cost` distinguishes chat-session totals from workflow totals
- workflow totals can exceed chat-session totals when news or other side-path usage exists

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_cli.py -k 'workflow_total or chat_session_tokens or truthful cost'`
Expected: FAIL because the CLI currently reports only one opaque total.

**Step 3: Commit**

```bash
git add tests/test_cli.py
git commit -m "test: require truthful token reporting"
```

### Task 8: Implement Truthful Token Reporting

**Files:**
- Modify: `archon/cli_repl_commands.py`
- Modify if needed: `archon/cli_ui.py`
- Modify if needed: `archon/agent.py`
- Test: `tests/test_cli.py`

**Step 1: Write the minimal implementation**

Update reporting so:
- `/status` keeps `tokens=` as session-chat tokens for continuity
- `/cost` reports both `chat_session_tokens` and `workflow_total_tokens`
- wording is explicit and does not imply full-system truth from the in-memory counters alone

Do not add pricing in this slice.

**Step 2: Run tests to verify it passes**

Run: `pytest -q tests/test_cli.py -k 'workflow_total or chat_session_tokens or truthful cost'`
Expected: PASS

**Step 3: Commit**

```bash
git add archon/cli_repl_commands.py archon/cli_ui.py archon/agent.py tests/test_cli.py
git commit -m "fix: report truthful workflow token totals"
```

### Task 9: Deep Research Accounting Policy Test and Guardrail

**Files:**
- Modify: `tests/test_research.py`
- Modify if needed: `archon/research/google_deep_research.py`
- Modify if needed: `archon/research/store.py`

**Step 1: Write the focused tests**

Add tests that prove:
- if Deep Research usage metadata is unavailable, no fake token event is recorded
- the summary/reporting layer remains truthful about what is included

**Step 2: Run test to verify expected behavior**

Run: `pytest -q tests/test_research.py -k 'usage unavailable or token accounting'`
Expected: either FAIL due to missing explicit guardrail, or PASS once the guardrail is implemented.

**Step 3: Implement minimal guardrail if needed**

Add only the smallest explicit behavior necessary so Deep Research accounting remains honest.

**Step 4: Re-run tests**

Run: `pytest -q tests/test_research.py -k 'usage unavailable or token accounting'`
Expected: PASS

**Step 5: Commit**

```bash
git add archon/research/google_deep_research.py archon/research/store.py tests/test_research.py
git commit -m "test: lock honest deep research token accounting"
```

### Task 10: Final Verification and Context Sync

**Files:**
- Modify: `CODEBASE_CONTEXT.json`
- Modify: `AGENT_CONTEXT.json`
- Modify if needed: `docs/plans/2026-03-11-token-usage-ledger-design.md`
- Modify if needed: `docs/plans/2026-03-11-token-usage-ledger-plan.md`

**Step 1: Run focused verification**

Run: `pytest -q tests/test_usage_store.py tests/test_agent.py tests/test_news_summarize.py tests/test_cli.py tests/test_research.py`
Expected: PASS

**Step 2: Run the full suite**

Run: `pytest -q tests`
Expected: PASS

**Step 3: Sync context**

Update context JSON files with:
- the new usage ledger architecture
- truthful token reporting behavior
- final verified test count

**Step 4: Commit**

```bash
git add CODEBASE_CONTEXT.json AGENT_CONTEXT.json docs/plans/2026-03-11-token-usage-ledger-design.md docs/plans/2026-03-11-token-usage-ledger-plan.md
git commit -m "docs: sync token usage ledger context"
```
