# Lightweight Context Control Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reduce prompt-token blowup in long Archon sessions by compacting earlier context proactively, storing much smaller tool history, and exposing shared context controls that work in both the CLI and Telegram.

**Architecture:** Keep the runtime lightweight. Reuse Archon's existing memory compaction path, usage ledger, and local command handlers instead of adding tokenizer dependencies or terminal-only UX. Add one shared context-pressure snapshot, shape verbose tool results before they enter model history, and trigger proactive compaction using provider-reported usage plus coarse local estimates.

**Tech Stack:** Python, existing `Agent` / `turn_executor` loop, CLI + Telegram adapters, pytest

---

### Task 1: Add failing regression tests for context pressure

**Files:**
- Modify: `tests/test_agent.py`
- Modify: `tests/test_agent_history_budget.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_telegram_adapter.py`

**Step 1: Add agent-loop regression tests**
- add a test that simulates a multi-iteration tool loop with large `shell` / `read_file` results and asserts later model calls do not keep growing linearly forever
- add a test that verifies high-pressure turns produce a compaction artifact before the next model call
- add a test that verifies the newest user turn plus newest tool-use/result pair survive compaction

**Step 2: Add local command regression tests**
- add a CLI `/context` test that expects actionable context details instead of only message count
- add a CLI `/status` or `/cost` test that reports the latest known context pressure fields
- add a Telegram local-command parity test for the richer `/context` output

**Step 3: Run focused tests to confirm they fail first**

Run:
```bash
python -m pytest tests/test_agent.py tests/test_agent_history_budget.py tests/test_cli.py tests/test_telegram_adapter.py -q -k 'context or compact or token or pressure'
```

### Task 2: Introduce a shared context snapshot helper

**Files:**
- Create: `archon/context_metrics.py`
- Modify: `archon/agent.py`
- Modify: `archon/cli_repl_commands.py`
- Modify: `tests/test_cli.py`

**Step 1: Add lightweight shared metrics**
- create a small helper module that builds a context snapshot from an `Agent`
- expose fields for `history_messages`, `history_chars`, `approx_history_tokens`, `pending_compactions`, `visible_tool_count`, `total_input_tokens`, `total_output_tokens`, and the latest known provider-reported `last_input_tokens`
- keep the estimator dependency-free; use existing character heuristics and provider-reported usage rather than adding tokenizers

**Step 2: Record the latest provider usage on the agent**
- store the latest `response.input_tokens` and `response.output_tokens` after each model call
- make the helper prefer provider-reported prompt usage when available and fall back to coarse local estimates otherwise

**Step 3: Upgrade local command output to use the shared snapshot**
- update `/context` to report compact but actionable context state
- update `/status` to include a lightweight pressure indicator
- keep `/cost` truthful and compact, but let it mention current chat-session totals and workflow totals without duplicating stale fields

**Step 4: Re-run targeted command tests**

Run:
```bash
python -m pytest tests/test_cli.py tests/test_telegram_adapter.py -q -k 'status or cost or context'
```

### Task 3: Separate human-visible tool output from model-visible history

**Files:**
- Modify: `archon/tooling/filesystem_tools.py`
- Modify: `archon/agent.py`
- Modify: `archon/execution/turn_executor.py`
- Modify: `tests/test_agent.py`

**Step 1: Make shell results more useful and more compact**
- change `shell()` to preserve an explicit exit status in the returned result
- keep the operator-facing output readable, but make sure the model can see whether the command succeeded without inferring it from free-form text

**Step 2: Add a dedicated history-shaping path**
- replace the current one-size-fits-all `_truncate_tool_result_for_history()` usage with a history shaper that accepts `tool_name`, tool arguments, and result text
- keep terminal / Telegram activity output unchanged
- store a much smaller history payload for verbose tools such as `shell`, `read_file`, `list_dir`, `glob`, and `grep`

**Step 3: Special-case the verbose tools**
- `shell`: keep command, exit code, and a short head/tail excerpt with an omitted-count marker
- `read_file`: keep path, requested range, and a short excerpt instead of thousands of numbered lines
- `list_dir` / `glob` / `grep`: keep counts plus the first useful matches instead of the entire result set

**Step 4: Run focused history-shaping tests**

Run:
```bash
python -m pytest tests/test_agent.py -q -k 'tool_result or shell or read_file or compact'
```

### Task 4: Add proactive compaction based on context pressure

**Files:**
- Modify: `archon/config.py`
- Modify: `archon/agent.py`
- Modify: `archon/execution/turn_executor.py`
- Modify: `tests/test_agent.py`
- Modify: `tests/test_agent_history_budget.py`
- Modify: `tests/test_config.py`

**Step 1: Add small config knobs for context pressure**
- add lightweight config values for prompt-pressure thresholds and compaction retention
- keep defaults conservative and provider-agnostic
- do not add model-specific lookup tables or heavyweight tokenization libraries

**Step 2: Trigger compaction before the next expensive model call**
- after each model response, store the observed prompt-token usage
- before the next iteration, compact older history when the latest prompt usage or local history estimate crosses the configured threshold
- preserve the newest in-flight reasoning context by keeping the latest user turn and latest tool interaction intact

**Step 3: Reuse existing compaction artifacts instead of inventing a second system**
- keep using `memory_store.compact_history()`
- keep injecting compaction summaries through the existing system-prompt path
- prefer dropping raw earlier turns once the compaction artifact is ready, mirroring the “keep the latest compacted window plus recent work” pattern

**Step 4: Run focused compaction tests**

Run:
```bash
python -m pytest tests/test_agent.py tests/test_agent_history_budget.py tests/test_config.py -q -k 'compact or budget or pressure'
```

### Task 5: Improve fresh-task controls with CLI and Telegram parity

**Files:**
- Modify: `archon/cli_commands.py`
- Modify: `archon/cli_repl_commands.py`
- Modify: `archon/cli_interactive_commands.py`
- Modify: `archon/adapters/telegram.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_telegram_adapter.py`

**Step 1: Add a Codex-style fresh-conversation entry point**
- add `/new` as an alias for “fresh chat context in the same session”
- keep existing `/clear` / `/reset` behavior intact, but make the fresh-task path obvious and discoverable

**Step 2: Keep context commands identical across CLI and Telegram**
- update help text, slash-command metadata, and Telegram bot commands so the same context-management commands are available in both surfaces
- keep the logic in shared local-command handlers rather than branching behavior by transport

**Step 3: Make pressure guidance actionable**
- when `/status` or `/context` detects elevated pressure, include a short recommendation such as `/compact` or `/new`
- avoid noisy unsolicited warnings; prefer explicit status surfaces first so Telegram does not become spammy

**Step 4: Re-run focused parity tests**

Run:
```bash
python -m pytest tests/test_cli.py tests/test_telegram_adapter.py -q -k 'new or help or status or context or compact'
```

### Task 6: Run end-to-end verification on the real loop

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `tests/test_agent.py`

**Step 1: Add one synthetic high-noise scenario**
- create a regression test that models the real failure mode: repeated inspection commands, repeated tool results, and one final answer
- assert that the final iterations run with smaller history than the naive “append everything forever” path

**Step 2: Run the focused suite**

Run:
```bash
python -m pytest tests/test_agent.py tests/test_agent_history_budget.py tests/test_cli.py tests/test_telegram_adapter.py tests/test_config.py -q
```

**Step 3: Run the full test suite**

Run:
```bash
XDG_STATE_HOME=/tmp/archon-state python -m pytest tests -q
```

**Step 4: Manual smoke check**
- start `archon chat`
- reproduce a noisy shell-heavy debugging turn
- verify `/status`, `/cost`, `/context`, `/compact`, and `/new`
- verify the same local context commands from Telegram still work without starting a model turn
