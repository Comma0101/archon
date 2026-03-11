# Archon Super Assistant v2 — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evolve Archon from a reactive chat agent into a growing super assistant with smart execution, project learning, human handoff, markdown skills, and session distillation.

**Architecture:** Four implementation layers, each building on the previous. Layer 1 (SmartLoop) fixes the execution spiral. Layer 2 (pause/resume + setup backend) enables human handoff. Layer 3 (markdown skills + project scanner) enables project learning. Layer 4 (distillation + heartbeat) enables growth. Every new concept compiles to an existing abstraction — no parallel systems.

**Tech Stack:** Python 3.11+, pytest, existing Archon modules (agent.py, turn_executor.py, memory.py, control/jobs.py, control/skills.py, config.py)

**Key Design Decisions (settled in discussion):**
- Per-backend job stores with `/jobs` aggregation (not a generic job store)
- Fresh-turn resume via wait records (not suspended threads)
- Markdown skills compile into existing `ProfileConfig` (not a parallel skill system)
- All memory distillation goes through `inbox_add` (review-first, never auto-write)
- LLM compaction writes to same `compactions/` path (not ephemeral summaries)
- Soft cost budget on main loop; best-effort on background paths
- Resume matching: auto-resume only when exactly one blocked setup job plausibly fits; otherwise require explicit job targeting or clarification

**Sources:** OpenClaw (skills, heartbeat), DeerFlow (sub-agent middleware, memory extraction), AstrBot (ToolLoopAgentRunner, LLM compression), Plandex (checkpoints), 12-Factor Agents (pause/resume, ask_human as tool), Mem0 (hierarchical memory), AGENTS.md (project documentation for agents)

---

## Codebase Review Corrections

This plan was reviewed against the current Archon codebase after the initial draft. The following constraints override any stale task text or illustrative code snippets below:

- SmartLoop must enforce per-tool timeout through a wrapped execution path, not by calling `agent.tools.execute(...)` directly with no timeout guard.
- Forced summarization must use a truly tool-disabled final LLM call. Any sample snippet that reuses the normal tool-enabled LLM path is illustrative only and must be adapted.
- `ask_human` suspension must use structured control flow (sentinel object/typed result/exception path), not a magic string prefix that can be truncated, redacted, or mistaken for normal tool output.
- Setup jobs need a setup-specific detail renderer for `/jobs show setup:<id>`, not just `JobSummary` aggregation.
- Markdown skills remain disk-authored, but runtime activation must compile into session profile activation and trigger matching. Do not treat markdown skills as peer built-ins by merging them directly into `BUILTIN_SKILLS`.
- Heartbeat requires a real runner plus notification wiring. Parsing checklist items alone is not sufficient.

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `archon/execution/smart_loop.py` | Budget-aware iteration with error classification, semantic loop detection, forced summarization |
| `archon/execution/diagnostics.py` | Error classification and diagnostic prompt injection |
| `archon/setup/models.py` | `SetupRecord` and `SetupStep` dataclasses |
| `archon/setup/store.py` | Persistence for project setup records (`~/.local/state/archon/setup/`) |
| `archon/setup/formatting.py` | Setup-specific `/jobs show setup:<id>` rendering |
| `archon/setup/scanner.py` | Project discovery scanner (reads README, deps, env files) |
| `archon/setup/assessor.py` | Capability assessment (archon_can / needs_human / already_done) |
| `archon/setup/resume.py` | Resume matching logic for blocked setup jobs |
| `archon/tooling/setup_tools.py` | `learn_project` and `ask_human` tool registrations plus structured suspension payloads |
| `archon/skills/loader.py` | Load SKILL.md folders and expose runtime metadata for trigger matching + session-profile compilation |
| `archon/skills/generator.py` | Auto-generate SKILL.md from successful sessions |
| `archon/memory/distiller.py` | LLM-powered session distillation → inbox entries |
| `archon/memory/compressor.py` | LLM-powered context compression → compaction artifacts |
| `archon/heartbeat.py` | Heartbeat runner (reads checklist, runs agent per item) |
| `tests/test_smart_loop.py` | SmartLoop unit tests |
| `tests/test_diagnostics.py` | Error classification tests |
| `tests/test_setup_models.py` | SetupRecord serialization tests |
| `tests/test_setup_store.py` | Setup store persistence tests |
| `tests/test_setup_scanner.py` | Project scanner tests |
| `tests/test_setup_assessor.py` | Capability assessment tests |
| `tests/test_setup_resume.py` | Resume matching tests |
| `tests/test_setup_tools.py` | Setup tool registration tests |
| `tests/test_skill_loader.py` | SKILL.md loading and compilation tests |
| `tests/test_skill_generator.py` | SKILL.md generation tests |
| `tests/test_distiller.py` | Session distillation tests |
| `tests/test_compressor.py` | LLM compression tests |
| `tests/test_heartbeat.py` | Heartbeat runner tests |

### Modified Files

| File | Change |
|------|--------|
| `archon/config.py` | Add `SmartLoopConfig`, `SetupConfig`, `SkillsConfig`, `HeartbeatConfig` dataclasses; add `SETUP_STATE_DIR`, `SKILLS_DIR` paths |
| `archon/execution/turn_executor.py` | Delegate to `smart_loop.run()` when smart loop enabled; keep current loop as fallback |
| `archon/agent.py` | Wire SmartLoop config; handle structured suspension results from tool execution |
| `archon/tools.py` | Register setup tools; add structured suspension passthrough in `execute()` |
| `archon/execution/contracts.py` | Add typed suspension/control result contract for tool-driven pause/resume |
| `archon/control/jobs.py` | Add `job_summary_from_setup_record()` converter |
| `archon/control/skills.py` | Add markdown-skill lookup helpers that compile loaded metadata into session profile activation |
| `archon/control/session_controller.py` | Add blocked-job detection for resume matching |
| `archon/cli_repl_commands.py:751` | Add `list_setup_job_summaries()` to `_collect_job_summaries()` |
| `archon/cli_repl_commands.py:762` | Add `setup:` prefix handling in `_load_job_summary()` and setup-specific detail rendering in `_render_job_detail()` |
| `archon/cli_repl_commands.py:781` | Add `"blocked"` to `_ACTIVE_JOB_STATUSES` |
| `archon/adapters/telegram.py` | Add `setup:` job reference routing |
| `archon/memory.py` | Add `compact_history_llm()` that writes LLM-generated compaction to same path |
| `archon/tooling/__init__.py` | Add `register_setup_tools` import |
| `archon/prompt.py` | Increase prefetch limit; add project runbook loading |
| `archon/cli_repl_commands.py` | Extend `_maybe_auto_activate_skill()` to consult markdown skill trigger matches and activate session profiles |

---

## Chunk 1: SmartLoop — Budget-Aware Execution

**Goal:** Replace the dumb `for i in range(max_iterations)` loop with budget-aware iteration that catches error spirals, enforces time/cost limits, and produces useful summaries at limits instead of `"[Iteration limit reached]"`.

### Task 1: SmartLoop Configuration

**Files:**
- Modify: `archon/config.py:42-55`
- Test: `tests/test_config.py` (existing, add cases)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_smart_loop.py
"""SmartLoop configuration and budget tests."""

from archon.config import SmartLoopConfig


def test_smart_loop_config_defaults():
    cfg = SmartLoopConfig()
    assert cfg.max_steps == 20
    assert cfg.wall_timeout_sec == 600
    assert cfg.tool_timeout_sec == 60
    assert cfg.turn_budget_tokens == 100_000
    assert cfg.max_consecutive_errors == 3
    assert cfg.error_diagnosis_threshold == 2
    assert cfg.enabled is False


def test_smart_loop_config_clamps_max_steps():
    cfg = SmartLoopConfig(max_steps=0)
    assert cfg.max_steps >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_smart_loop.py::test_smart_loop_config_defaults -v`
Expected: FAIL — `SmartLoopConfig` does not exist

- [ ] **Step 3: Implement SmartLoopConfig**

Add to `archon/config.py` after `AgentConfig`:

```python
@dataclass
class SmartLoopConfig:
    enabled: bool = False
    max_steps: int = 20
    wall_timeout_sec: float = 600.0
    tool_timeout_sec: float = 60.0
    turn_budget_tokens: int = 100_000
    max_consecutive_errors: int = 3
    error_diagnosis_threshold: int = 2

    def __post_init__(self) -> None:
        self.max_steps = max(1, int(self.max_steps))
        self.wall_timeout_sec = max(10.0, float(self.wall_timeout_sec))
        self.tool_timeout_sec = max(5.0, float(self.tool_timeout_sec))
        self.turn_budget_tokens = max(1000, int(self.turn_budget_tokens))
        self.max_consecutive_errors = max(1, int(self.max_consecutive_errors))
        self.error_diagnosis_threshold = max(1, int(self.error_diagnosis_threshold))
```

Add `smart_loop: SmartLoopConfig = field(default_factory=SmartLoopConfig)` to the `Config` dataclass. Wire TOML loading in `_load_config()` under `[smart_loop]` section.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_smart_loop.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add archon/config.py tests/test_smart_loop.py
git commit -m "feat: add SmartLoopConfig with budget defaults"
```

### Task 2: Error Classification

**Files:**
- Create: `archon/execution/diagnostics.py`
- Test: `tests/test_diagnostics.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_diagnostics.py
"""Error classification and diagnostic prompt injection."""

from archon.execution.diagnostics import classify_error, build_diagnostic_hint


def test_classify_transient_error():
    assert classify_error("Error: 503 Service Unavailable") == "transient"
    assert classify_error("Error: Connection timed out") == "transient"
    assert classify_error("Error: 429 rate limit exceeded") == "transient"


def test_classify_environmental_error():
    assert classify_error("Error: command not found: bun") == "environmental"
    assert classify_error("Error: No module named 'browser_use'") == "environmental"
    assert classify_error("Error: ENOENT: no such file or directory") == "environmental"


def test_classify_permission_error():
    assert classify_error("Error: Permission denied") == "permission"
    assert classify_error("Error: EACCES: permission denied") == "permission"


def test_classify_fundamental_error():
    assert classify_error("Error: Invalid API key") == "fundamental"
    assert classify_error("Error: Authentication failed") == "fundamental"


def test_classify_unknown_error():
    assert classify_error("Error: something weird happened") == "unknown"
    assert classify_error("just some text") == "none"


def test_build_diagnostic_hint():
    hint = build_diagnostic_hint(consecutive_errors=2, last_error="Error: command not found: bun")
    assert "DIAGNOSTIC" in hint
    assert "environmental" in hint.lower() or "classify" in hint.lower()


def test_build_diagnostic_hint_below_threshold():
    hint = build_diagnostic_hint(consecutive_errors=1, last_error="Error: oops")
    assert hint == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_diagnostics.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement diagnostics module**

```python
# archon/execution/diagnostics.py
"""Error classification and diagnostic prompt injection for the smart loop."""

import re

_TRANSIENT_PATTERNS = [
    re.compile(r"\b(503|502|500|504|429)\b"),
    re.compile(r"\b(timeout|timed?\s*out|unavailable|rate.?limit|temporar|try.?again)\b", re.I),
]

_ENVIRONMENTAL_PATTERNS = [
    re.compile(r"\bcommand not found\b", re.I),
    re.compile(r"\bNo module named\b", re.I),
    re.compile(r"\bENOENT\b"),
    re.compile(r"\bnot installed\b", re.I),
    re.compile(r"\bno such file or directory\b", re.I),
    re.compile(r"\bMissing dependency\b", re.I),
]

_PERMISSION_PATTERNS = [
    re.compile(r"\b[Pp]ermission denied\b"),
    re.compile(r"\bEACCES\b"),
    re.compile(r"\bOperation not permitted\b", re.I),
]

_FUNDAMENTAL_PATTERNS = [
    re.compile(r"\b[Ii]nvalid.{0,10}(API|api).?key\b"),
    re.compile(r"\b[Aa]uthentication failed\b"),
    re.compile(r"\b[Uu]nauthorized\b"),
    re.compile(r"\b[Ff]orbidden\b"),
]


def classify_error(text: str) -> str:
    """Classify an error message. Returns: transient|environmental|permission|fundamental|unknown|none."""
    if not text or not str(text).strip().lower().startswith("error"):
        return "none"
    for pat in _TRANSIENT_PATTERNS:
        if pat.search(text):
            return "transient"
    for pat in _PERMISSION_PATTERNS:
        if pat.search(text):
            return "permission"
    for pat in _FUNDAMENTAL_PATTERNS:
        if pat.search(text):
            return "fundamental"
    for pat in _ENVIRONMENTAL_PATTERNS:
        if pat.search(text):
            return "environmental"
    return "unknown"


def build_diagnostic_hint(
    *,
    consecutive_errors: int,
    last_error: str,
    threshold: int = 2,
) -> str:
    """Build a diagnostic prompt hint if error threshold is reached."""
    if consecutive_errors < threshold:
        return ""
    classification = classify_error(last_error)
    return (
        f"\n[DIAGNOSTIC] You have hit {consecutive_errors} consecutive errors."
        f"\nLast error classified as: {classification}."
        f"\nLast error: {last_error[:300]}"
        "\nBefore your next action:"
        "\n1. Is this error transient (retry may help) or structural (need a different approach)?"
        "\n2. If environmental (missing tool/dep): can you install it, or must the user act?"
        "\n3. If fundamental (wrong credentials/approach): STOP and explain what the user needs to fix."
        "\n4. If you cannot diagnose: STOP and report what you know."
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_diagnostics.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add archon/execution/diagnostics.py tests/test_diagnostics.py
git commit -m "feat: add error classification and diagnostic prompt injection"
```

### Task 3: Semantic Loop Detection

**Files:**
- Modify: `archon/execution/diagnostics.py`
- Test: `tests/test_diagnostics.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_diagnostics.py

from archon.execution.diagnostics import detect_semantic_spiral


def test_detect_spiral_all_errors():
    results = ["Error: fail1", "Error: fail2", "Error: fail3", "Error: fail4"]
    assert detect_semantic_spiral(results, window=4) is True


def test_detect_spiral_mixed_results():
    results = ["Success", "Error: fail", "Success", "Error: fail"]
    assert detect_semantic_spiral(results, window=4) is False


def test_detect_spiral_too_few():
    results = ["Error: fail1", "Error: fail2"]
    assert detect_semantic_spiral(results, window=4) is False


def test_detect_spiral_empty():
    assert detect_semantic_spiral([], window=4) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_diagnostics.py::test_detect_spiral_all_errors -v`
Expected: FAIL — function does not exist

- [ ] **Step 3: Implement semantic spiral detection**

Add to `archon/execution/diagnostics.py`:

```python
def detect_semantic_spiral(recent_results: list[str], window: int = 4) -> bool:
    """Detect when all recent tool results are errors (regardless of content)."""
    if len(recent_results) < window:
        return False
    tail = recent_results[-window:]
    return all(str(r).strip().startswith("Error:") for r in tail)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_diagnostics.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add archon/execution/diagnostics.py tests/test_diagnostics.py
git commit -m "feat: add semantic spiral detection for error-heavy tool loops"
```

### Task 4: SmartLoop Core Implementation

**Files:**
- Create: `archon/execution/smart_loop.py`
- Test: `tests/test_smart_loop.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_smart_loop.py

import time
from unittest.mock import MagicMock, patch
from archon.execution.smart_loop import SmartLoop, SmartLoopResult
from archon.config import SmartLoopConfig


def _make_mock_agent(max_chars=48000):
    agent = MagicMock()
    agent.history = []
    agent.total_input_tokens = 0
    agent.total_output_tokens = 0
    agent.tools = MagicMock()
    agent.config = MagicMock()
    agent.hooks = MagicMock()
    agent._truncate_tool_result_for_history = MagicMock(side_effect=lambda n, t: t)
    agent._enforce_iteration_budget = MagicMock()
    agent._emit_hook = MagicMock()
    agent._make_assistant_msg = MagicMock(side_effect=lambda r: {"role": "assistant", "content": r.text or ""})
    return agent


def test_smart_loop_budget_exceeded():
    cfg = SmartLoopConfig(enabled=True, turn_budget_tokens=100)
    loop = SmartLoop(cfg)
    # LLM returns tool call, consuming > 100 tokens
    response = MagicMock()
    response.tool_calls = [MagicMock(name="shell", id="t1", arguments={"command": "ls"})]
    response.text = None
    response.input_tokens = 80
    response.output_tokens = 80
    response.raw_content = []

    agent = _make_mock_agent()
    agent.tools.execute = MagicMock(return_value="file1.txt")

    result = loop.run(agent, response_fn=lambda _: response, system_prompt="test")
    assert result.reason in ("budget_exceeded", "budget_tokens")
    assert "budget" in result.text.lower() or "token" in result.text.lower()


def test_smart_loop_consecutive_errors_bail():
    cfg = SmartLoopConfig(enabled=True, max_consecutive_errors=2)
    loop = SmartLoop(cfg)
    response = MagicMock()
    response.tool_calls = [MagicMock(name="shell", id="t1", arguments={"command": "bad"})]
    response.text = None
    response.input_tokens = 10
    response.output_tokens = 10
    response.raw_content = []

    agent = _make_mock_agent()
    agent.tools.execute = MagicMock(return_value="Error: command not found")

    result = loop.run(agent, response_fn=lambda _: response, system_prompt="test")
    assert result.reason == "consecutive_errors"


def test_smart_loop_normal_completion():
    cfg = SmartLoopConfig(enabled=True)
    loop = SmartLoop(cfg)
    response = MagicMock()
    response.tool_calls = []
    response.text = "Done!"
    response.input_tokens = 10
    response.output_tokens = 10
    response.raw_content = "Done!"

    agent = _make_mock_agent()
    result = loop.run(agent, response_fn=lambda _: response, system_prompt="test")
    assert result.reason == "completed"
    assert result.text == "Done!"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_smart_loop.py::test_smart_loop_budget_exceeded -v`
Expected: FAIL — `SmartLoop` does not exist

- [ ] **Step 3: Implement SmartLoop**

```python
# archon/execution/smart_loop.py
"""Budget-aware execution loop with error classification and forced summarization."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from archon.config import SmartLoopConfig
from archon.execution.diagnostics import (
    build_diagnostic_hint,
    classify_error,
    detect_semantic_spiral,
)
from archon.llm import LLMResponse
from archon.security.redaction import redact_secret_like_text

if TYPE_CHECKING:
    from archon.agent import Agent


@dataclass
class SmartLoopResult:
    text: str
    reason: str  # completed | budget_tokens | wall_timeout | consecutive_errors | spiral | forced_summary | step_limit
    steps_used: int
    tokens_used: int
    errors_hit: int


class SmartLoop:
    def __init__(self, config: SmartLoopConfig):
        self.config = config

    def run(
        self,
        agent: "Agent",
        *,
        response_fn: Callable[[str], LLMResponse],
        system_prompt: str,
        turn_id: str = "",
        active_profile: str = "default",
        log_prefix: str = "",
    ) -> SmartLoopResult:
        from archon.agent import _detect_tool_loop, _print_tool_call, _print_tool_result
        from archon.control.policy import evaluate_tool_policy

        cfg = self.config
        start_time = time.monotonic()
        turn_tokens = 0
        consecutive_errors = 0
        recent_tool_calls: list[tuple[str, dict]] = []
        recent_results: list[str] = []
        last_error = ""

        for step in range(cfg.max_steps):
            # --- Budget checks BEFORE LLM call ---
            if turn_tokens > cfg.turn_budget_tokens:
                return self._make_result(
                    agent, "budget_tokens", step, turn_tokens, consecutive_errors,
                    f"Token budget exceeded ({turn_tokens:,} tokens used). "
                    "Stopping to control cost. Here is what I accomplished so far and what remains.",
                )

            elapsed = time.monotonic() - start_time
            if elapsed > cfg.wall_timeout_sec:
                return self._make_result(
                    agent, "wall_timeout", step, turn_tokens, consecutive_errors,
                    f"Time limit reached ({elapsed:.0f}s). "
                    "Stopping. Here is what I accomplished and what remains.",
                )

            # --- Build iteration prompt ---
            iter_prompt = system_prompt
            if step > 0:
                iter_prompt += (
                    f"\n\n[Step {step + 1}/{cfg.max_steps}. "
                    "Be targeted — do not repeat previous approaches.]"
                )

            diagnostic = build_diagnostic_hint(
                consecutive_errors=consecutive_errors,
                last_error=last_error,
                threshold=cfg.error_diagnosis_threshold,
            )
            if diagnostic:
                iter_prompt += diagnostic

            # --- At step limit: force summarization (AstrBot pattern) ---
            if step == cfg.max_steps - 1:
                return self._forced_summary(
                    agent, response_fn_no_tools, iter_prompt, step, turn_tokens, consecutive_errors,
                )

            # --- LLM call ---
            if agent.on_thinking:
                agent.on_thinking()

            response = response_fn(iter_prompt)
            turn_tokens += response.input_tokens + response.output_tokens
            agent.total_input_tokens += response.input_tokens
            agent.total_output_tokens += response.output_tokens

            # --- No tool calls = done ---
            if not response.tool_calls:
                text = response.text or ""
                agent.history.append(agent._make_assistant_msg(response))
                return SmartLoopResult(
                    text=text, reason="completed",
                    steps_used=step + 1, tokens_used=turn_tokens,
                    errors_hit=consecutive_errors,
                )

            # --- Execute tools ---
            agent.history.append(agent._make_assistant_msg(response))
            tool_results = []

            for call in response.tool_calls:
                policy = evaluate_tool_policy(
                    config=agent.config, tool_name=call.name,
                    mode="implement", profile_name=active_profile,
                )
                agent._emit_hook("policy.decision", {
                    "turn_id": turn_id, "name": call.name,
                    "decision": policy.decision, "reason": policy.reason,
                    "profile": policy.profile, "mode": policy.mode,
                })

                if policy.decision == "deny":
                    result_text = f"Error: Policy denied tool '{call.name}' ({policy.reason})"
                else:
                    _print_tool_call(
                        call.name, call.arguments, prefix=log_prefix,
                        activity_feed=getattr(agent, "terminal_activity_feed", None),
                    )
                    if agent.on_tool_call:
                        agent.on_tool_call(call.name, call.arguments)

                    raw_result = self._execute_tool_with_timeout(
                        agent,
                        call,
                        timeout_sec=cfg.tool_timeout_sec,
                    )
                    if isinstance(raw_result, SuspensionRequest):
                        agent.history.append({"role": "user", "content": tool_results})
                        return SmartLoopResult(
                            text=raw_result.question,
                            reason="suspended",
                            steps_used=step + 1,
                            tokens_used=turn_tokens,
                            errors_hit=consecutive_errors,
                        )
                    result_text = redact_secret_like_text(str(raw_result))
                    _print_tool_result(
                        result_text, prefix=log_prefix,
                        activity_feed=getattr(agent, "terminal_activity_feed", None),
                    )

                # Track errors
                if result_text.startswith("Error:"):
                    consecutive_errors += 1
                    last_error = result_text
                else:
                    consecutive_errors = 0

                recent_results.append(result_text)
                if len(recent_results) > 10:
                    recent_results = recent_results[-10:]

                history_result = agent._truncate_tool_result_for_history(call.name, result_text)
                tool_results.append({
                    "type": "tool_result", "tool_use_id": call.id,
                    "tool_name": call.name, "content": history_result,
                })

            # --- Loop detection (both exact-match and semantic) ---
            for call in response.tool_calls:
                recent_tool_calls.append((call.name, call.arguments))
            if len(recent_tool_calls) > 10:
                recent_tool_calls = recent_tool_calls[-10:]

            if _detect_tool_loop(recent_tool_calls):
                return self._make_result(
                    agent, "exact_loop", step, turn_tokens, consecutive_errors,
                    "I notice I am repeating the same actions. Let me stop and reassess.",
                )

            if detect_semantic_spiral(recent_results):
                return self._make_result(
                    agent, "spiral", step, turn_tokens, consecutive_errors,
                    f"I have hit {len([r for r in recent_results[-4:] if r.startswith('Error:')])} "
                    f"consecutive errors with different approaches. Last error: {last_error[:200]}. "
                    "Stopping to avoid wasting tokens. Here is what I think needs fixing.",
                )

            # --- Consecutive error bail-out ---
            if consecutive_errors >= cfg.max_consecutive_errors:
                error_class = classify_error(last_error)
                return self._make_result(
                    agent, "consecutive_errors", step, turn_tokens, consecutive_errors,
                    f"Hit {consecutive_errors} consecutive errors (classified: {error_class}). "
                    f"Last error: {last_error[:300]}. "
                    "Stopping. Here is what you may need to fix before I can continue.",
                )

            # --- Append tool results and enforce budget ---
            agent.history.append({"role": "user", "content": tool_results})
            agent._enforce_iteration_budget()

        return self._make_result(
            agent, "step_limit", cfg.max_steps, turn_tokens, consecutive_errors,
            f"[Step limit reached after {cfg.max_steps} steps]",
        )

    def _forced_summary(
        self, agent, response_fn_no_tools, prompt, step, turn_tokens, errors,
    ) -> SmartLoopResult:
        """AstrBot pattern: at limit, disable tools and ask for a summary."""
        summary_prompt = (
            prompt
            + "\n\n[FINAL STEP] You have reached the step limit. "
            "Summarize what you accomplished, what failed, and what the user "
            "should do next. Do NOT call any tools."
        )
        if agent.on_thinking:
            agent.on_thinking()
        response = response_fn_no_tools(summary_prompt)
        turn_tokens += response.input_tokens + response.output_tokens
        agent.total_input_tokens += response.input_tokens
        agent.total_output_tokens += response.output_tokens
        text = response.text or "[No summary generated]"
        agent.history.append(agent._make_assistant_msg(response))
        return SmartLoopResult(
            text=text, reason="forced_summary",
            steps_used=step + 1, tokens_used=turn_tokens,
            errors_hit=errors,
        )

    def _make_result(
        self, agent, reason, steps, tokens, errors, text,
    ) -> SmartLoopResult:
        agent.history.append({"role": "assistant", "content": text})
        return SmartLoopResult(
            text=text, reason=reason,
            steps_used=steps, tokens_used=tokens,
            errors_hit=errors,
        )
```

Implementation note: the real `SmartLoop.run(...)` should accept a normal LLM closure and a separate `response_fn_no_tools` closure for the forced-summary path. It should also execute tools through a helper such as `_execute_tool_with_timeout(...)` instead of calling the registry directly inline.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_smart_loop.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add archon/execution/smart_loop.py tests/test_smart_loop.py
git commit -m "feat: implement SmartLoop with budget, error, and spiral guards"
```

### Task 5: Wire SmartLoop into Turn Executor

**Files:**
- Modify: `archon/execution/turn_executor.py:15-35`
- Modify: `archon/agent.py:145-195`
- Test: `tests/test_agent.py` (existing, add integration test)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_smart_loop.py (append)

def test_smart_loop_wired_via_config():
    """SmartLoop is used when config.smart_loop.enabled is True."""
    from archon.config import Config, SmartLoopConfig
    cfg = Config()
    cfg.smart_loop = SmartLoopConfig(enabled=True, max_steps=5)
    assert cfg.smart_loop.enabled is True
    assert cfg.smart_loop.max_steps == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_smart_loop.py::test_smart_loop_wired_via_config -v`
Expected: FAIL — Config has no `smart_loop` attribute yet

- [ ] **Step 3: Wire SmartLoop into the execution path**

In `archon/execution/turn_executor.py`, add at the top of `execute_turn()`:

```python
def execute_turn(agent, *, turn_id, user_message, active_profile, log_prefix,
                 turn_system_prompt, llm_step):
    # SmartLoop gate: use budget-aware loop when enabled
    smart_loop_cfg = getattr(getattr(agent.config, "smart_loop", None), None)
    if smart_loop_cfg is not None and getattr(smart_loop_cfg, "enabled", False):
        from archon.execution.smart_loop import SmartLoop
        loop = SmartLoop(smart_loop_cfg)
        result = loop.run(
            agent,
            response_fn=llm_step,
            system_prompt=turn_system_prompt,
            turn_id=turn_id,
            active_profile=active_profile,
            log_prefix=log_prefix,
        )
        return result.text

    # ... existing loop code unchanged ...
```

Do the same for `execute_turn_stream()`, yielding `result.text`.

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: all existing tests still pass, new tests pass

- [ ] **Step 5: Commit**

```bash
git add archon/config.py archon/execution/turn_executor.py tests/test_smart_loop.py
git commit -m "feat: wire SmartLoop into turn executor behind config gate"
```

---

## Chunk 2: Setup Backend — Models, Store, Job Integration

**Goal:** Add the `project_setup` job backend with persistence, job summary conversion, and `/jobs` integration. This is the data foundation for project learning and human handoff.

### Task 6: Setup Data Models

**Files:**
- Create: `archon/setup/models.py`
- Create: `archon/setup/__init__.py`
- Test: `tests/test_setup_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_setup_models.py
"""SetupRecord and SetupStep serialization tests."""

from archon.setup.models import SetupRecord, SetupStep


def test_setup_step_roundtrip():
    step = SetupStep(
        step_id=1, kind="archon", description="Install deps",
        status="done", hint="", env_var="", provided=False,
    )
    d = step.to_dict()
    restored = SetupStep.from_dict(d)
    assert restored.step_id == 1
    assert restored.kind == "archon"
    assert restored.status == "done"


def test_setup_record_roundtrip():
    record = SetupRecord(
        setup_id="setup-browser-use-20260310",
        project_name="browser-use",
        project_path="~/Documents/browser-use",
        status="blocked",
        created_at="2026-03-10T14:30:00Z",
        updated_at="2026-03-10T14:32:00Z",
        stack="Python 3.11 + browser automation",
        steps=[
            SetupStep(1, "archon", "Install deps", "done", "", "", False),
            SetupStep(2, "human", "Provide OPENAI_API_KEY", "pending",
                      "Sign up at https://platform.openai.com", "OPENAI_API_KEY", False),
        ],
        discovery_sources=["README.md", "pyproject.toml"],
        generated_skill_path="",
    )
    d = record.to_dict()
    restored = SetupRecord.from_dict(d)
    assert restored.setup_id == "setup-browser-use-20260310"
    assert restored.status == "blocked"
    assert len(restored.steps) == 2
    assert restored.steps[1].env_var == "OPENAI_API_KEY"


def test_setup_record_blocked_steps():
    record = SetupRecord(
        setup_id="test", project_name="test", project_path="/tmp",
        status="blocked", created_at="", updated_at="", stack="",
        steps=[
            SetupStep(1, "archon", "step1", "done", "", "", False),
            SetupStep(2, "human", "step2", "pending", "hint", "KEY", False),
            SetupStep(3, "archon", "step3", "pending", "", "", False),
        ],
        discovery_sources=[], generated_skill_path="",
    )
    blocked = record.blocked_steps()
    assert len(blocked) == 1
    assert blocked[0].step_id == 2

    pending = record.pending_archon_steps()
    assert len(pending) == 1
    assert pending[0].step_id == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_setup_models.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement setup models**

```python
# archon/setup/__init__.py
"""Project setup and learning subsystem."""

# archon/setup/models.py
"""Project setup record data models."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SetupStep:
    step_id: int
    kind: str  # archon | human
    description: str
    status: str  # pending | in_progress | done | failed | skipped
    hint: str  # help text for human steps
    env_var: str  # environment variable name if applicable
    provided: bool  # whether human has provided the input

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "kind": self.kind,
            "description": self.description,
            "status": self.status,
            "hint": self.hint,
            "env_var": self.env_var,
            "provided": self.provided,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SetupStep:
        return cls(
            step_id=int(data.get("step_id", 0)),
            kind=str(data.get("kind", "archon")),
            description=str(data.get("description", "")),
            status=str(data.get("status", "pending")),
            hint=str(data.get("hint", "")),
            env_var=str(data.get("env_var", "")),
            provided=bool(data.get("provided", False)),
        )


@dataclass
class SetupRecord:
    setup_id: str
    project_name: str
    project_path: str
    status: str  # pending | in_progress | blocked | completed | failed
    created_at: str
    updated_at: str
    stack: str
    steps: list[SetupStep] = field(default_factory=list)
    discovery_sources: list[str] = field(default_factory=list)
    generated_skill_path: str = ""

    def blocked_steps(self) -> list[SetupStep]:
        return [s for s in self.steps if s.kind == "human" and s.status == "pending" and not s.provided]

    def pending_archon_steps(self) -> list[SetupStep]:
        return [s for s in self.steps if s.kind == "archon" and s.status == "pending"]

    def all_human_steps_done(self) -> bool:
        return all(s.provided or s.status == "done" for s in self.steps if s.kind == "human")

    def to_dict(self) -> dict:
        return {
            "setup_id": self.setup_id,
            "project_name": self.project_name,
            "project_path": self.project_path,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "stack": self.stack,
            "steps": [s.to_dict() for s in self.steps],
            "discovery_sources": list(self.discovery_sources),
            "generated_skill_path": self.generated_skill_path,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SetupRecord:
        steps_raw = data.get("steps", [])
        steps = [SetupStep.from_dict(s) for s in steps_raw if isinstance(s, dict)]
        return cls(
            setup_id=str(data.get("setup_id", "")),
            project_name=str(data.get("project_name", "")),
            project_path=str(data.get("project_path", "")),
            status=str(data.get("status", "pending")),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            stack=str(data.get("stack", "")),
            steps=steps,
            discovery_sources=list(data.get("discovery_sources", [])),
            generated_skill_path=str(data.get("generated_skill_path", "")),
        )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_setup_models.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add archon/setup/__init__.py archon/setup/models.py tests/test_setup_models.py
git commit -m "feat: add SetupRecord and SetupStep data models"
```

### Task 7: Setup Store (Persistence)

**Files:**
- Create: `archon/setup/store.py`
- Modify: `archon/config.py` (add `SETUP_STATE_DIR`)
- Test: `tests/test_setup_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_setup_store.py
"""Setup store persistence tests."""

import json
from pathlib import Path
from archon.setup.models import SetupRecord, SetupStep
from archon.setup.store import save_setup_record, load_setup_record, list_setup_records, list_setup_job_summaries


def _make_record(setup_id="test-1", status="pending"):
    return SetupRecord(
        setup_id=setup_id, project_name="test-project",
        project_path="/tmp/test", status=status,
        created_at="2026-03-10T14:00:00Z", updated_at="2026-03-10T14:00:00Z",
        stack="Python", steps=[], discovery_sources=[], generated_skill_path="",
    )


def test_save_and_load(tmp_path, monkeypatch):
    monkeypatch.setattr("archon.setup.store._setup_dir", lambda: tmp_path)
    record = _make_record()
    save_setup_record(record)
    loaded = load_setup_record("test-1")
    assert loaded is not None
    assert loaded.setup_id == "test-1"
    assert loaded.project_name == "test-project"


def test_load_nonexistent(tmp_path, monkeypatch):
    monkeypatch.setattr("archon.setup.store._setup_dir", lambda: tmp_path)
    assert load_setup_record("nonexistent") is None


def test_list_records(tmp_path, monkeypatch):
    monkeypatch.setattr("archon.setup.store._setup_dir", lambda: tmp_path)
    save_setup_record(_make_record("a", "completed"))
    save_setup_record(_make_record("b", "blocked"))
    records = list_setup_records(limit=10)
    assert len(records) == 2


def test_list_job_summaries(tmp_path, monkeypatch):
    monkeypatch.setattr("archon.setup.store._setup_dir", lambda: tmp_path)
    save_setup_record(_make_record("x", "blocked"))
    summaries = list_setup_job_summaries(limit=10)
    assert len(summaries) == 1
    assert summaries[0].job_id == "setup:x"
    assert summaries[0].kind == "project_setup"
    assert summaries[0].status == "blocked"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_setup_store.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement setup store**

Add to `archon/config.py`:

```python
SETUP_STATE_DIR = STATE_DIR / "setup"
```

```python
# archon/setup/store.py
"""Persistence for project setup records."""

from __future__ import annotations

import json
from pathlib import Path

from archon.config import SETUP_STATE_DIR
from archon.control.jobs import JobSummary
from archon.setup.models import SetupRecord


def _setup_dir() -> Path:
    d = SETUP_STATE_DIR / "records"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_setup_record(record: SetupRecord) -> None:
    path = _setup_dir() / f"{record.setup_id}.json"
    path.write_text(json.dumps(record.to_dict(), indent=2, sort_keys=True))


def load_setup_record(setup_id: str) -> SetupRecord | None:
    path = _setup_dir() / f"{setup_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return SetupRecord.from_dict(data)
    except Exception:
        return None


def list_setup_records(limit: int = 10) -> list[SetupRecord]:
    d = _setup_dir()
    records: list[SetupRecord] = []
    for path in sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text())
            records.append(SetupRecord.from_dict(data))
        except Exception:
            continue
        if len(records) >= limit:
            break
    return records


def list_setup_job_summaries(limit: int = 10) -> list[JobSummary]:
    records = list_setup_records(limit)
    return [_to_summary(r) for r in records]


def load_setup_job_summary(setup_id: str) -> JobSummary | None:
    record = load_setup_record(setup_id)
    if record is None:
        return None
    return _to_summary(record)


def list_blocked_setup_records() -> list[SetupRecord]:
    return [r for r in list_setup_records(limit=50) if r.status == "blocked"]


def _to_summary(record: SetupRecord) -> JobSummary:
    done = sum(1 for s in record.steps if s.status == "done")
    total = len(record.steps)
    blocked = len(record.blocked_steps())
    summary_parts = [f"{record.project_name}: {done}/{total} steps"]
    if blocked:
        summary_parts.append(f"waiting for {blocked} human step(s)")
    return JobSummary(
        job_id=f"setup:{record.setup_id}",
        kind="project_setup",
        status=record.status,
        summary=", ".join(summary_parts),
        last_update_at=record.updated_at or record.created_at,
    )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_setup_store.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add archon/config.py archon/setup/store.py tests/test_setup_store.py
git commit -m "feat: add setup store with persistence and job summary conversion"
```

### Task 8: Wire Setup Backend into /jobs Aggregation

**Files:**
- Modify: `archon/cli_repl_commands.py:751-778`
- Create: `archon/setup/formatting.py`
- Modify: `archon/cli_repl_commands.py:781` (add "blocked" to active statuses)
- Modify: `archon/control/jobs.py` (add `job_summary_from_setup_record`)
- Test: `tests/test_setup_store.py` (extend with setup detail rendering)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_setup_store.py (append)

def test_blocked_is_active_status():
    from archon.cli_repl_commands import _ACTIVE_JOB_STATUSES
    assert "blocked" in _ACTIVE_JOB_STATUSES


def test_format_setup_record_shows_blocked_and_pending_steps():
    from archon.setup.formatting import format_setup_record
    record = SetupRecord(
        setup_id="browser-use",
        project_name="browser-use",
        project_path="/tmp/browser-use",
        status="blocked",
        created_at="2026-03-10T14:30:00Z",
        updated_at="2026-03-10T14:32:00Z",
        stack="Python",
        steps=[
            SetupStep(1, "archon", "Install deps", "done", "", "", False),
            SetupStep(2, "human", "Provide OPENAI_API_KEY", "pending", "Sign up first", "OPENAI_API_KEY", False),
            SetupStep(3, "archon", "Verify install", "pending", "", "", False),
        ],
        discovery_sources=["README.md"],
        generated_skill_path="",
    )
    text = format_setup_record(record)
    assert "setup_id: browser-use" in text
    assert "setup_status: blocked" in text
    assert "Provide OPENAI_API_KEY" in text
    assert "Verify install" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_setup_store.py::test_blocked_is_active_status -v`
Expected: FAIL — "blocked" not in the set

- [ ] **Step 3: Wire setup into /jobs**

In `archon/cli_repl_commands.py`:

1. Add import: `from archon.setup.store import list_setup_job_summaries, load_setup_job_summary`

2. In `_collect_job_summaries()` (line 751), add:
```python
items.extend(list_setup_job_summaries(limit=max_items))
```

3. In `_load_job_summary()` (line 762), add before the fallback block:
```python
if ref.startswith("setup:"):
    return load_setup_job_summary(ref.split(":", 1)[1])
```

4. Add `archon/setup/formatting.py` with a setup-specific detail renderer:
```python
def format_setup_record(record: SetupRecord) -> str:
    lines = [
        f"setup_id: {record.setup_id}",
        f"setup_project: {record.project_name}",
        f"setup_status: {record.status}",
        f"setup_stack: {record.stack}",
        f"setup_sources: {', '.join(record.discovery_sources) or '(none)'}",
    ]
    blocked = record.blocked_steps()
    if blocked:
        lines.append("blocked_steps:")
        for step in blocked:
            lines.append(
                f"- [{step.step_id}] {step.description}"
                + (f" | env_var={step.env_var}" if step.env_var else "")
                + (f" | hint={step.hint}" if step.hint else "")
            )
    pending = record.pending_archon_steps()
    if pending:
        lines.append("pending_archon_steps:")
        for step in pending:
            lines.append(f"- [{step.step_id}] {step.description}")
    return "\n".join(lines)
```

5. In `_render_job_detail()` in `archon/cli_repl_commands.py`, add a dedicated `setup:` branch before the generic fallback:
```python
if ref.startswith("setup:"):
    record = load_setup_record(ref.split(":", 1)[1])
    if record is None:
        return f"Job not found: {ref}"
    from archon.setup.formatting import format_setup_record
    return format_setup_record(record)
```

6. In `_ACTIVE_JOB_STATUSES` (line 781), add `"blocked"`.

In `archon/control/jobs.py`, add the converter (for external use):
```python
def job_summary_from_setup_record(record: "SetupRecord") -> JobSummary:
    from archon.setup.store import _to_summary
    return _to_summary(record)
```

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add archon/setup/formatting.py archon/cli_repl_commands.py archon/control/jobs.py tests/test_setup_store.py
git commit -m "feat: wire setup backend into /jobs aggregation with blocked status"
```

---

## Chunk 3: Resume Matching & ask_human Tool

**Goal:** Implement the resume matching logic for blocked jobs and the `ask_human` tool that returns a structured suspension request interpreted by the agent loop.

### Task 9: Resume Matching Logic

**Files:**
- Create: `archon/setup/resume.py`
- Test: `tests/test_setup_resume.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_setup_resume.py
"""Resume matching for blocked setup jobs."""

from archon.setup.models import SetupRecord, SetupStep
from archon.setup.resume import match_input_to_blocked_job, MatchResult


def _make_blocked_record(setup_id, project, env_var, hint=""):
    return SetupRecord(
        setup_id=setup_id, project_name=project, project_path=f"/tmp/{project}",
        status="blocked", created_at="", updated_at="", stack="",
        steps=[
            SetupStep(1, "archon", "Install", "done", "", "", False),
            SetupStep(2, "human", f"Provide {env_var}", "pending", hint, env_var, False),
        ],
        discovery_sources=[], generated_skill_path="",
    )


def test_single_match():
    records = [_make_blocked_record("s1", "browser-use", "OPENAI_API_KEY")]
    result = match_input_to_blocked_job("here's the API key: sk-abc123", records)
    assert result.kind == "single_match"
    assert result.job.setup_id == "s1"


def test_project_named_single_match():
    records = [
        _make_blocked_record("s1", "browser-use", "OPENAI_API_KEY"),
        _make_blocked_record("s2", "hedge-fund", "ALPACA_KEY"),
    ]
    result = match_input_to_blocked_job("browser-use API key is sk-abc", records)
    assert result.kind == "single_match"
    assert result.job.setup_id == "s1"


def test_ambiguous_match():
    records = [
        _make_blocked_record("s1", "project-a", "API_KEY"),
        _make_blocked_record("s2", "project-b", "API_KEY"),
    ]
    result = match_input_to_blocked_job("here is the API key", records)
    assert result.kind == "ambiguous"
    assert len(result.candidates) == 2


def test_no_match():
    records = [_make_blocked_record("s1", "browser-use", "OPENAI_API_KEY")]
    result = match_input_to_blocked_job("what's the weather today", records)
    assert result.kind == "no_match"


def test_no_blocked_jobs():
    result = match_input_to_blocked_job("here's a key", [])
    assert result.kind == "no_blocked_jobs"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_setup_resume.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement resume matching**

```python
# archon/setup/resume.py
"""Resume matching for blocked setup jobs."""

from __future__ import annotations

from dataclasses import dataclass, field

from archon.setup.models import SetupRecord


@dataclass
class MatchResult:
    kind: str  # no_blocked_jobs | no_match | single_match | ambiguous
    job: SetupRecord | None = None
    candidates: list[SetupRecord] = field(default_factory=list)
    matched_step_id: int | None = None


def match_input_to_blocked_job(
    user_message: str,
    blocked_records: list[SetupRecord],
) -> MatchResult:
    if not blocked_records:
        return MatchResult(kind="no_blocked_jobs")

    msg_lower = user_message.lower()
    scored: list[tuple[SetupRecord, float, int | None]] = []

    for record in blocked_records:
        score = 0.0
        best_step_id = None

        # Project name match
        if record.project_name.lower() in msg_lower:
            score += 10.0

        # Check each blocked step
        for step in record.blocked_steps():
            step_score = 0.0
            # Env var name match
            if step.env_var and step.env_var.lower() in msg_lower:
                step_score += 8.0
            # Partial env var match (e.g., "api key" matches "OPENAI_API_KEY")
            env_words = set(step.env_var.lower().replace("_", " ").split()) if step.env_var else set()
            msg_words = set(msg_lower.split())
            overlap = env_words & msg_words
            if overlap:
                step_score += len(overlap) * 2.0
            # Description keyword match
            desc_words = set(step.description.lower().split())
            desc_overlap = desc_words & msg_words
            if desc_overlap:
                step_score += len(desc_overlap) * 1.0

            if step_score > 0 and (best_step_id is None or step_score > score):
                best_step_id = step.step_id

            score += step_score

        # Generic signals (key, token, secret, password, credential)
        generic_signals = {"key", "token", "secret", "password", "credential", "done", "ready", "completed"}
        if generic_signals & msg_words and score == 0:
            score += 1.0  # weak generic signal

        if score > 0:
            scored.append((record, score, best_step_id))

    if not scored:
        return MatchResult(kind="no_match")

    scored.sort(key=lambda x: -x[1])

    if len(scored) == 1:
        return MatchResult(kind="single_match", job=scored[0][0], matched_step_id=scored[0][2])

    return MatchResult(kind="ambiguous", candidates=[s[0] for s in scored[:5]])
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_setup_resume.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add archon/setup/resume.py tests/test_setup_resume.py
git commit -m "feat: add resume matching for blocked setup jobs"
```

### Task 10: ask_human and learn_project Tool Registration

**Files:**
- Create: `archon/tooling/setup_tools.py`
- Modify: `archon/execution/contracts.py`
- Modify: `archon/tooling/__init__.py` (add import)
- Modify: `archon/tools.py` (register)
- Test: `tests/test_setup_tools.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_setup_tools.py
"""Setup tool registration tests."""

from archon.execution.contracts import SuspensionRequest


def test_ask_human_returns_suspension(tmp_path, monkeypatch):
    """ask_human returns a structured suspension request."""
    monkeypatch.setattr("archon.setup.store._setup_dir", lambda: tmp_path)
    from archon.tooling.setup_tools import _ask_human_noninteractive
    result = _ask_human_noninteractive(
        question="Please provide OPENAI_API_KEY",
        context="Sign up at https://platform.openai.com",
        project="browser-use",
    )
    assert isinstance(result, SuspensionRequest)
    assert result.question == "Please provide OPENAI_API_KEY"
    assert result.project == "browser-use"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_setup_tools.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement setup tools**

```python
# archon/execution/contracts.py
@dataclass
class SuspensionRequest:
    """Structured tool-driven pause request returned to the execution loop."""

    reason: str
    question: str
    context: str = ""
    project: str = ""
    job_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

# archon/tooling/setup_tools.py
"""Project learning and human handoff tool registrations."""

from __future__ import annotations

from archon.execution.contracts import SuspensionRequest


def _ask_human_noninteractive(
    question: str,
    context: str = "",
    project: str = "",
) -> SuspensionRequest:
    """Create a structured suspension request for async human handoff."""
    return SuspensionRequest(
        reason="needs_human_input",
        question=question,
        context=context,
        project=project,
    )


def register_setup_tools(registry) -> None:
    def ask_human(
        question: str,
        context: str = "",
        project: str = "",
    ) -> SuspensionRequest:
        """Ask the human to perform a step Archon cannot do."""
        # Return a typed pause request. The execution loop persists it into a
        # blocked setup job and the surface can present the human handoff.
        return _ask_human_noninteractive(question, context, project)

    registry.register(
        "ask_human",
        "Ask the user to perform a step you cannot do (provide API keys, "
        "complete authentication, manual actions). Returns a structured suspension request "
        "that pauses the current task. The user will be notified and can resume later.",
        {
            "properties": {
                "question": {
                    "type": "string",
                    "description": "What you need the human to do",
                },
                "context": {
                    "type": "string",
                    "description": "Why you need this and any helpful links/instructions",
                    "default": "",
                },
                "project": {
                    "type": "string",
                    "description": "Related project name, if any",
                    "default": "",
                },
            },
            "required": ["question"],
        },
        ask_human,
    )

    def learn_project(project_path: str) -> str:
        """Scan a project directory and build an operational profile.

        Reads README, package files, env templates, and other discovery
        files to understand the project's stack, dependencies, and setup
        requirements. Use this when the user asks to learn, set up, or
        understand an open-source project.
        """
        from archon.setup.scanner import scan_project
        try:
            profile = scan_project(project_path)
            return profile.to_summary()
        except Exception as e:
            return f"Error scanning project: {e}"

    registry.register(
        "learn_project",
        "Scan a project directory to learn its stack, dependencies, setup steps, "
        "and requirements. Use when the user asks to learn, set up, or understand "
        "a project under ~/Documents/ or elsewhere.",
        {
            "properties": {
                "project_path": {
                    "type": "string",
                    "description": "Absolute path to the project directory",
                },
            },
            "required": ["project_path"],
        },
        learn_project,
    )
```

Add to `archon/tooling/__init__.py`:

```python
from archon.tooling.setup_tools import register_setup_tools
```

Add to `archon/tools.py` in `_register_builtins()`:

```python
register_setup_tools(self)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_setup_tools.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add archon/execution/contracts.py archon/tooling/setup_tools.py archon/tooling/__init__.py archon/tools.py tests/test_setup_tools.py
git commit -m "feat: add ask_human and learn_project tools with structured suspension"
```

### Task 11: Structured Suspension Handling in Agent Loop

**Files:**
- Modify: `archon/execution/smart_loop.py` (detect structured suspension in tool results)
- Modify: `archon/execution/turn_executor.py` (same for legacy loop)
- Modify: `archon/tools.py` (allow typed passthrough instead of forcing stringification)
- Test: `tests/test_smart_loop.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_smart_loop.py (append)

def test_smart_loop_handles_suspension():
    """When a tool returns a structured suspension request, the loop stops gracefully."""
    from archon.execution.contracts import SuspensionRequest
    cfg = SmartLoopConfig(enabled=True)
    loop = SmartLoop(cfg)

    response = MagicMock()
    response.tool_calls = [MagicMock(name="ask_human", id="t1",
                                      arguments={"question": "Provide key"})]
    response.text = None
    response.input_tokens = 10
    response.output_tokens = 10
    response.raw_content = []

    agent = _make_mock_agent()
    agent.tools.execute = MagicMock(
        return_value=SuspensionRequest(
            reason="needs_human_input",
            question="Provide key",
            project="browser-use",
        )
    )

    result = loop.run(agent, response_fn=lambda _: response, system_prompt="test")
    assert result.reason == "suspended"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_smart_loop.py::test_smart_loop_handles_suspension -v`
Expected: FAIL — SmartLoop doesn't handle suspension yet

- [ ] **Step 3: Add structured suspension handling to SmartLoop**

In `archon/execution/smart_loop.py`, after tool execution in the loop, add:

```python
from archon.execution.contracts import SuspensionRequest

# Inside the tool execution loop, after getting raw_result from registry.execute():
if isinstance(raw_result, SuspensionRequest):
    # Tool requested human handoff — suspend the turn and return control metadata.
    agent.history.append({"role": "user", "content": tool_results})
    return SmartLoopResult(
        text=raw_result.question,
        reason="suspended",
        steps_used=step + 1,
        tokens_used=turn_tokens,
        errors_hit=consecutive_errors,
    )
```

Update the implementation shape so `ToolRegistry.execute()` can return either a normal string result or a `SuspensionRequest`. Do not encode control flow in a magic string prefix.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_smart_loop.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add archon/tools.py archon/execution/smart_loop.py archon/execution/turn_executor.py tests/test_smart_loop.py
git commit -m "feat: handle structured suspension requests in execution loops"
```

---

## Chunk 4: Project Scanner & Capability Assessment

**Goal:** Implement the project discovery scanner that reads README, dependency files, and env templates to build an operational profile, and the capability assessor that classifies what Archon can do alone vs. needs human help.

### Task 12: Project Scanner

**Files:**
- Create: `archon/setup/scanner.py`
- Test: `tests/test_setup_scanner.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_setup_scanner.py
"""Project scanner tests."""

from pathlib import Path
from archon.setup.scanner import scan_project, ProjectProfile


def test_scan_python_project(tmp_path):
    (tmp_path / "README.md").write_text("# My Project\nA Python web app.\n## Setup\npip install -r requirements.txt\n")
    (tmp_path / "requirements.txt").write_text("fastapi\nuvicorn\n")
    (tmp_path / ".env.example").write_text("DATABASE_URL=\nSECRET_KEY=\n")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "myapp"\nrequires-python = ">=3.11"\n')

    profile = scan_project(str(tmp_path))
    assert profile.project_name == tmp_path.name
    assert "README.md" in profile.discovery_sources
    assert "pyproject.toml" in profile.discovery_sources
    assert "DATABASE_URL" in profile.env_vars
    assert "SECRET_KEY" in profile.env_vars


def test_scan_node_project(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "myapp", "scripts": {"dev": "next dev", "build": "next build"}}')
    profile = scan_project(str(tmp_path))
    assert "dev" in profile.scripts
    assert "build" in profile.scripts


def test_scan_empty_project(tmp_path):
    profile = scan_project(str(tmp_path))
    assert profile.project_name == tmp_path.name
    assert len(profile.discovery_sources) == 0


def test_profile_to_summary(tmp_path):
    (tmp_path / "README.md").write_text("# Test\nA test project.\n")
    profile = scan_project(str(tmp_path))
    summary = profile.to_summary()
    assert "Test" in summary or tmp_path.name in summary
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_setup_scanner.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement project scanner**

```python
# archon/setup/scanner.py
"""Project discovery scanner — reads source files to build an operational profile."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


DISCOVERY_FILES = [
    "README.md", "AGENTS.md",
    "package.json", "pyproject.toml", "setup.py", "setup.cfg",
    "Makefile", "Justfile",
    "docker-compose.yml", "docker-compose.yaml",
    ".env.example", ".env.template", ".env.sample",
    "Cargo.toml", "go.mod",
    ".tool-versions", ".nvmrc", ".python-version",
    "requirements.txt", "Pipfile",
]


@dataclass
class ProjectProfile:
    project_path: str
    project_name: str
    discovery_sources: list[str] = field(default_factory=list)
    source_contents: dict[str, str] = field(default_factory=dict)
    env_vars: list[str] = field(default_factory=list)
    scripts: dict[str, str] = field(default_factory=dict)
    readme_text: str = ""
    stack_hints: list[str] = field(default_factory=list)

    def to_summary(self) -> str:
        lines = [f"Project: {self.project_name}", f"Path: {self.project_path}"]
        if self.discovery_sources:
            lines.append(f"Discovered files: {', '.join(self.discovery_sources)}")
        if self.stack_hints:
            lines.append(f"Stack: {', '.join(self.stack_hints)}")
        if self.scripts:
            lines.append("Scripts:")
            for name, cmd in self.scripts.items():
                lines.append(f"  {name}: {cmd}")
        if self.env_vars:
            lines.append(f"Required env vars: {', '.join(self.env_vars)}")
        if self.readme_text:
            excerpt = self.readme_text[:1500]
            lines.append(f"\nREADME excerpt:\n{excerpt}")
        return "\n".join(lines)


def scan_project(project_path: str) -> ProjectProfile:
    """Scan a project directory and build an operational profile."""
    path = Path(project_path).expanduser().resolve()
    profile = ProjectProfile(
        project_path=str(path),
        project_name=path.name,
    )

    for filename in DISCOVERY_FILES:
        filepath = path / filename
        if filepath.exists() and filepath.is_file():
            try:
                text = filepath.read_text(errors="replace")[:10000]
                profile.discovery_sources.append(filename)
                profile.source_contents[filename] = text
            except Exception:
                continue

    # Extract env vars from .env templates
    for env_file in (".env.example", ".env.template", ".env.sample"):
        text = profile.source_contents.get(env_file, "")
        if text:
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    var_name = line.split("=", 1)[0].strip()
                    if var_name and var_name not in profile.env_vars:
                        profile.env_vars.append(var_name)

    # Extract scripts from package.json
    pkg_text = profile.source_contents.get("package.json", "")
    if pkg_text:
        try:
            pkg = json.loads(pkg_text)
            scripts = pkg.get("scripts", {})
            if isinstance(scripts, dict):
                profile.scripts = {k: str(v) for k, v in scripts.items()}
            # Stack hints
            deps = set(pkg.get("dependencies", {}).keys()) | set(pkg.get("devDependencies", {}).keys())
            if "next" in deps:
                profile.stack_hints.append("Next.js")
            if "react" in deps:
                profile.stack_hints.append("React")
            if "vue" in deps:
                profile.stack_hints.append("Vue")
        except Exception:
            pass

    # Extract stack hints from pyproject.toml
    pyproject_text = profile.source_contents.get("pyproject.toml", "")
    if pyproject_text:
        profile.stack_hints.append("Python")
        if "fastapi" in pyproject_text.lower():
            profile.stack_hints.append("FastAPI")
        if "django" in pyproject_text.lower():
            profile.stack_hints.append("Django")
        if "flask" in pyproject_text.lower():
            profile.stack_hints.append("Flask")

    # Other stack hints
    if "Cargo.toml" in profile.discovery_sources:
        profile.stack_hints.append("Rust")
    if "go.mod" in profile.discovery_sources:
        profile.stack_hints.append("Go")
    if "requirements.txt" in profile.discovery_sources and "Python" not in profile.stack_hints:
        profile.stack_hints.append("Python")

    # README
    profile.readme_text = profile.source_contents.get("README.md", "")

    return profile
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_setup_scanner.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add archon/setup/scanner.py tests/test_setup_scanner.py
git commit -m "feat: add project scanner for discovery file analysis"
```

### Task 13: Capability Assessor

**Files:**
- Create: `archon/setup/assessor.py`
- Test: `tests/test_setup_assessor.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_setup_assessor.py
"""Capability assessment tests."""

import os
from archon.setup.scanner import ProjectProfile
from archon.setup.assessor import assess_capabilities, AssessmentResult


def test_env_var_already_set(monkeypatch):
    monkeypatch.setenv("EXISTING_KEY", "value")
    profile = ProjectProfile(
        project_path="/tmp/test", project_name="test",
        env_vars=["EXISTING_KEY"],
    )
    result = assess_capabilities(profile)
    assert len(result.already_done) == 1
    assert "EXISTING_KEY" in result.already_done[0]


def test_sensitive_env_var_needs_human():
    profile = ProjectProfile(
        project_path="/tmp/test", project_name="test",
        env_vars=["OPENAI_API_KEY", "DATABASE_URL"],
    )
    result = assess_capabilities(profile)
    human_vars = [s.env_var for s in result.needs_human if s.env_var]
    assert "OPENAI_API_KEY" in human_vars


def test_assessment_result_to_steps():
    profile = ProjectProfile(
        project_path="/tmp/test", project_name="test",
        env_vars=["SECRET_KEY"],
    )
    result = assess_capabilities(profile)
    steps = result.to_setup_steps()
    assert len(steps) > 0
    assert any(s.kind == "human" for s in steps)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_setup_assessor.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement capability assessor**

```python
# archon/setup/assessor.py
"""Capability assessment — what Archon can do alone vs. needs human help."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from archon.setup.models import SetupStep
from archon.setup.scanner import ProjectProfile


_SENSITIVE_ENV_PATTERNS = {
    "key", "token", "secret", "password", "credential",
    "auth", "api_key", "apikey", "private",
}


@dataclass
class HumanRequirement:
    what: str
    why: str
    how: str
    env_var: str = ""
    hint: str = ""


@dataclass
class AssessmentResult:
    already_done: list[str] = field(default_factory=list)
    archon_can: list[str] = field(default_factory=list)
    needs_human: list[HumanRequirement] = field(default_factory=list)

    def to_setup_steps(self) -> list[SetupStep]:
        steps: list[SetupStep] = []
        step_id = 1

        for desc in self.archon_can:
            steps.append(SetupStep(
                step_id=step_id, kind="archon", description=desc,
                status="pending", hint="", env_var="", provided=False,
            ))
            step_id += 1

        for req in self.needs_human:
            steps.append(SetupStep(
                step_id=step_id, kind="human", description=req.what,
                status="pending", hint=req.hint or req.how,
                env_var=req.env_var, provided=False,
            ))
            step_id += 1

        return steps


def assess_capabilities(profile: ProjectProfile) -> AssessmentResult:
    result = AssessmentResult()

    # Assess environment variables
    for var in profile.env_vars:
        if os.environ.get(var):
            result.already_done.append(f"{var} is already set")
        elif _is_sensitive(var):
            result.needs_human.append(HumanRequirement(
                what=f"Provide {var}",
                why=f"Required by {profile.project_name}",
                how=f"Set: export {var}=your_value",
                env_var=var,
                hint=_signup_hint(var),
            ))
        else:
            result.archon_can.append(f"Set {var} from project defaults or config")

    # Assess install steps from scripts
    if profile.scripts:
        if "install" in profile.scripts or any("install" in v for v in profile.scripts.values()):
            result.archon_can.append("Run install command")
    elif "requirements.txt" in profile.discovery_sources:
        result.archon_can.append("Install Python dependencies (pip install -r requirements.txt)")
    elif "Cargo.toml" in profile.discovery_sources:
        result.archon_can.append("Build Rust project (cargo build)")

    return result


def _is_sensitive(var_name: str) -> bool:
    lower = var_name.lower()
    return any(pat in lower for pat in _SENSITIVE_ENV_PATTERNS)


def _signup_hint(var_name: str) -> str:
    lower = var_name.lower()
    if "openai" in lower:
        return "Sign up at https://platform.openai.com/api-keys"
    if "anthropic" in lower:
        return "Sign up at https://console.anthropic.com/"
    if "google" in lower or "gemini" in lower:
        return "Get key at https://aistudio.google.com/apikey"
    return ""
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_setup_assessor.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add archon/setup/assessor.py tests/test_setup_assessor.py
git commit -m "feat: add capability assessor for project setup requirements"
```

---

## Chunk 5: Markdown Skills — Loader, Trigger Matching, and Session Profile Activation

**Goal:** Load SKILL.md folders from `~/.local/share/archon/skills/`, preserve trigger metadata, and compile matched markdown skills into the existing session-profile activation path. One skill system, two authoring formats.

### Task 14: Skill Loader

**Files:**
- Create: `archon/skills/__init__.py`
- Create: `archon/skills/loader.py`
- Modify: `archon/config.py` (add `SKILLS_DIR`)
- Test: `tests/test_skill_loader.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_skill_loader.py
"""SKILL.md loading and compilation tests."""

from pathlib import Path
from archon.skills.loader import load_markdown_skills, MarkdownSkill


def test_load_skill_from_folder(tmp_path):
    skill_dir = tmp_path / "deploy-korami"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("""---
name: deploy-korami
description: Deploy korami-site to Vercel
triggers:
  - deploy korami
  - push korami to production
requires:
  bins: [bun, vercel]
  env: [VERCEL_TOKEN]
tools: [shell, read_file]
timeout: 300
---

## Steps

1. cd ~/Documents/korami-site
2. Run bun install
3. Run bun run build
4. Run vercel --prod
""")

    skills = load_markdown_skills(tmp_path)
    assert len(skills) == 1
    skill = skills[0]
    assert skill.name == "deploy-korami"
    assert "deploy korami" in skill.triggers
    assert "shell" in skill.allowed_tools
    assert skill.timeout == 300


def test_load_skill_without_frontmatter(tmp_path):
    skill_dir = tmp_path / "simple"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Simple Skill\nJust some instructions.\n")
    skills = load_markdown_skills(tmp_path)
    assert len(skills) == 1
    assert skills[0].name == "simple"  # falls back to folder name


def test_load_empty_dir(tmp_path):
    skills = load_markdown_skills(tmp_path)
    assert skills == []


def test_skill_to_builtin():
    from archon.skills.loader import MarkdownSkill
    skill = MarkdownSkill(
        name="test", description="A test", triggers=["do test"],
        allowed_tools=["shell"], requires_bins=[], requires_env=[],
        timeout=60, content="## Steps\n1. Do thing\n",
    )
    profile = skill.to_profile_kwargs()
    assert profile["skill_name"] == "test"
    assert "shell" in profile["allowed_tools"]
    assert "## Steps" in profile["prompt_guidance"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_skill_loader.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement skill loader**

Add to `archon/config.py`:

```python
SKILLS_DIR = DATA_DIR / "skills"
```

```python
# archon/skills/__init__.py
"""Markdown skill loading subsystem."""

# archon/skills/loader.py
"""Load SKILL.md folders and expose runtime metadata for skill activation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

@dataclass
class MarkdownSkill:
    name: str
    description: str = ""
    triggers: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=lambda: ["*"])
    requires_bins: list[str] = field(default_factory=list)
    requires_env: list[str] = field(default_factory=list)
    timeout: int = 300
    content: str = ""  # the markdown body after frontmatter

    def to_profile_kwargs(self) -> dict[str, Any]:
        guidance = self.content.strip()
        if self.description and self.description not in guidance:
            guidance = f"{self.description}\n\n{guidance}"
        return {
            "skill_name": self.name,
            "allowed_tools": list(self.allowed_tools) if self.allowed_tools else ["*"],
            "prompt_guidance": guidance,
            "triggers": list(self.triggers),
            "timeout": self.timeout,
        }


def load_markdown_skills(skills_dir: Path) -> list[MarkdownSkill]:
    """Scan a directory for SKILL.md folders and load them."""
    if not skills_dir.exists():
        return []

    skills: list[MarkdownSkill] = []
    for child in sorted(skills_dir.iterdir()):
        if not child.is_dir():
            continue
        skill_file = child / "SKILL.md"
        if not skill_file.exists():
            continue
        try:
            skill = _parse_skill_file(skill_file, folder_name=child.name)
            skills.append(skill)
        except Exception:
            continue
    return skills


def _parse_skill_file(path: Path, folder_name: str) -> MarkdownSkill:
    text = path.read_text(errors="replace")
    frontmatter, body = _split_frontmatter(text)

    name = str(frontmatter.get("name", folder_name)).strip() or folder_name
    description = str(frontmatter.get("description", "")).strip()
    triggers = _as_list(frontmatter.get("triggers", []))
    tools = _as_list(frontmatter.get("tools", ["*"]))
    timeout = int(frontmatter.get("timeout", 300))

    requires = frontmatter.get("requires", {})
    if isinstance(requires, dict):
        bins = _as_list(requires.get("bins", []))
        env = _as_list(requires.get("env", []))
    else:
        bins, env = [], []

    return MarkdownSkill(
        name=name, description=description, triggers=triggers,
        allowed_tools=tools, requires_bins=bins, requires_env=env,
        timeout=timeout, content=body,
    )


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Split YAML frontmatter from markdown body."""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)", text, re.DOTALL)
    if not match:
        return {}, text

    yaml_text = match.group(1)
    body = match.group(2)

    # Minimal YAML parser (no PyYAML dependency) for simple key-value + lists
    data = _parse_simple_yaml(yaml_text)
    return data, body


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse simple YAML (key: value, key: [list], nested one level)."""
    result: dict[str, Any] = {}
    current_key = ""
    current_list: list[str] | None = None
    current_dict: dict[str, Any] | None = None

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())

        # List item under a key
        if stripped.startswith("- ") and current_key and indent > 0:
            value = stripped[2:].strip()
            if current_list is not None:
                current_list.append(value)
            continue

        # Nested key: value under a parent key
        if ":" in stripped and indent > 0 and current_dict is not None:
            k, _, v = stripped.partition(":")
            v = v.strip()
            if v.startswith("[") and v.endswith("]"):
                current_dict[k.strip()] = [x.strip() for x in v[1:-1].split(",") if x.strip()]
            else:
                current_dict[k.strip()] = v
            continue

        # Top-level key: value
        if ":" in stripped and indent == 0:
            if current_key and current_list is not None:
                result[current_key] = current_list
            elif current_key and current_dict is not None:
                result[current_key] = current_dict

            k, _, v = stripped.partition(":")
            current_key = k.strip()
            v = v.strip()
            current_list = None
            current_dict = None

            if v.startswith("[") and v.endswith("]"):
                result[current_key] = [x.strip() for x in v[1:-1].split(",") if x.strip()]
                current_key = ""
            elif v:
                result[current_key] = v
                current_key = ""
            else:
                # Could be start of list or nested dict — peek ahead
                current_list = []
                current_dict = {}

    # Flush last
    if current_key:
        if current_list:
            result[current_key] = current_list
        elif current_dict:
            result[current_key] = current_dict

    return result


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_skill_loader.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add archon/config.py archon/skills/__init__.py archon/skills/loader.py tests/test_skill_loader.py
git commit -m "feat: add SKILL.md loader with YAML frontmatter parsing"
```

### Task 15: Wire Markdown Skills into Existing Skill System

**Files:**
- Modify: `archon/control/skills.py` (add markdown skill lookup + session-profile compilation helpers)
- Modify: `archon/cli_repl_commands.py` (teach `_maybe_auto_activate_skill()` to consult markdown trigger matches)
- Test: `tests/test_skill_loader.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_skill_loader.py (append)

def test_markdown_skill_trigger_match(tmp_path, monkeypatch):
    """A markdown skill can be matched from natural-language triggers."""
    from archon.control.skills import find_markdown_skill_match, _loaded_markdown_skills
    skill_dir = tmp_path / "my-deploy"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-deploy\ntriggers:\n  - deploy my app\n---\nDeploy stuff.\n"
    )

    monkeypatch.setattr("archon.control.skills._MARKDOWN_SKILLS_DIR", tmp_path)
    _loaded_markdown_skills.cache_clear()  # clear any cached state

    skill = find_markdown_skill_match("please deploy my app now")
    assert skill is not None
    assert skill.name == "my-deploy"

    _loaded_markdown_skills.cache_clear()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_skill_loader.py::test_markdown_skill_trigger_match -v`
Expected: FAIL — markdown skill cache/lookup helpers not found

- [ ] **Step 3: Integrate markdown skills into skills.py**

Add to `archon/control/skills.py`:

```python
import functools
from archon.config import SKILLS_DIR

_MARKDOWN_SKILLS_DIR = SKILLS_DIR


@functools.lru_cache(maxsize=1)
def _loaded_markdown_skills() -> dict[str, MarkdownSkill]:
    """Load and cache markdown skills from disk."""
    try:
        from archon.skills.loader import load_markdown_skills
        skills = load_markdown_skills(_MARKDOWN_SKILLS_DIR)
        return {s.name: s for s in skills}
    except Exception:
        return {}


def find_markdown_skill_match(text: str) -> MarkdownSkill | None:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return None
    for skill in _loaded_markdown_skills().values():
        for trigger in skill.triggers:
            if trigger and trigger.lower() in lowered:
                return skill
    return None


def ensure_markdown_session_skill_profile(config, *, skill_name: str, base_profile_name: str = "default") -> str:
    skill = _loaded_markdown_skills().get(str(skill_name or "").strip().lower())
    if skill is None:
        raise ValueError(f"Unknown markdown skill '{skill_name}'")
    profile_name = make_session_skill_profile_name(base_profile_name, skill.name)
    config.profiles[profile_name] = ProfileConfig(
        allowed_tools=skill.to_profile_kwargs()["allowed_tools"],
        max_mode="implement",
        execution_backend="host",
        skill=skill.name,
    )
    return profile_name
```

In `archon/cli_repl_commands.py`, update `_maybe_auto_activate_skill()` so it:

1. keeps the current explicit built-in skill activation behavior
2. checks `find_markdown_skill_match(text)` when no built-in skill request matched
3. activates the resulting markdown skill by creating a session profile, not by merging it into `BUILTIN_SKILLS`

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add archon/control/skills.py archon/cli_repl_commands.py tests/test_skill_loader.py
git commit -m "feat: activate markdown skills through session profile matching"
```

---

## Chunk 6: Memory Improvements — LLM Compaction & Retrieval Upgrade

**Goal:** Replace mechanical compaction with LLM-powered summarization (written to same compaction path) and increase memory retrieval surface area.

### Task 16: LLM-Powered Compaction

**Files:**
- Create: `archon/memory/compressor.py`
- Modify: `archon/memory.py:674` (add `compact_history_llm` alongside existing)
- Test: `tests/test_compressor.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_compressor.py
"""LLM-powered context compression tests."""

from archon.memory.compressor import build_compression_prompt, parse_compression_result


def test_build_compression_prompt():
    messages = [
        {"role": "user", "content": "deploy korami"},
        {"role": "assistant", "content": "I'll deploy korami-site using bun and vercel."},
    ]
    prompt = build_compression_prompt(messages)
    assert "deploy" in prompt.lower() or "summarize" in prompt.lower()
    assert "korami" in prompt


def test_parse_compression_result():
    llm_output = "User asked to deploy korami-site. Archon ran bun build successfully."
    result = parse_compression_result(llm_output, layer="session", summary_id="test-1")
    assert result["layer"] == "session"
    assert "korami" in result["content"]
    assert result["summary_id"] == "test-1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_compressor.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement LLM compressor**

```python
# archon/memory/__init__.py
"""Memory subsystem extensions."""

# archon/memory/compressor.py
"""LLM-powered context compression that writes to the same compaction path."""

from __future__ import annotations


COMPRESSION_SYSTEM_PROMPT = (
    "You are a context compression assistant. Summarize the conversation "
    "preserving:\n"
    "- What task was being worked on and its current state\n"
    "- Key decisions made and why\n"
    "- Errors encountered and their root causes\n"
    "- Important facts discovered (project details, paths, commands)\n"
    "- What remains to be done\n\n"
    "Be concise but preserve actionable information. Write in past tense."
)


def build_compression_prompt(messages: list[dict], max_chars: int = 8000) -> str:
    """Build a prompt for LLM-based compression of conversation history."""
    lines = ["Summarize this conversation history:\n"]
    total_chars = 0
    for msg in messages:
        role = str(msg.get("role", "unknown"))
        content = _flatten(msg.get("content"))
        entry = f"{role}: {content}"
        if total_chars + len(entry) > max_chars:
            entry = entry[:max_chars - total_chars]
            lines.append(entry)
            break
        lines.append(entry)
        total_chars += len(entry)
    return "\n".join(lines)


def parse_compression_result(
    llm_output: str,
    *,
    layer: str = "session",
    summary_id: str = "latest",
) -> dict:
    """Package LLM output into a compaction artifact dict."""
    content = llm_output.strip()
    if not content:
        content = "(No summary generated)"
    title = "# Session Compaction Summary" if layer == "session" else "# Task Compaction Summary"
    return {
        "layer": layer,
        "summary_id": summary_id,
        "content": f"{title}\n\n{content}\n",
        "summary": content[:240],
    }


def _flatten(content: object, max_chars: int = 500) -> str:
    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                t = item.get("type", "")
                if t == "text":
                    parts.append(str(item.get("text", "")))
                elif t == "tool_use":
                    parts.append(f"[tool: {item.get('name', '')}]")
                elif t == "tool_result":
                    parts.append(f"[result: {str(item.get('content', ''))[:100]}]")
            elif isinstance(item, str):
                parts.append(item)
        text = " ".join(parts).strip()
    else:
        text = str(content or "").strip()
    return text[:max_chars] if len(text) > max_chars else text
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_compressor.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add archon/memory/__init__.py archon/memory/compressor.py tests/test_compressor.py
git commit -m "feat: add LLM-powered compression prompt builder and result parser"
```

### Task 17: Increase Memory Retrieval Surface

**Files:**
- Modify: `archon/agent.py:956-978` (increase prefetch limits)
- Modify: `archon/memory.py:468-504` (increase defaults)
- Test: existing memory tests should still pass

- [ ] **Step 1: Write the failing test**

```python
# tests/test_compressor.py (append)

def test_prefetch_defaults_increased():
    """Memory prefetch should now return more results with larger excerpts."""
    from archon.memory import prefetch_for_query
    # Verify the function signature accepts the increased defaults
    import inspect
    sig = inspect.signature(prefetch_for_query)
    limit_default = sig.parameters["limit"].default
    max_chars_default = sig.parameters["max_chars_per_file"].default
    assert limit_default >= 4, f"prefetch limit should be >= 4, got {limit_default}"
    assert max_chars_default >= 1500, f"max_chars should be >= 1500, got {max_chars_default}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_compressor.py::test_prefetch_defaults_increased -v`
Expected: FAIL — current defaults are limit=2, max_chars=1000

- [ ] **Step 3: Increase defaults**

In `archon/memory.py`, modify `prefetch_for_query()` signature:

```python
def prefetch_for_query(
    query: str,
    limit: int = 4,           # was 2
    min_score: float = 5.0,   # was 6.0 (slightly more permissive)
    max_lines_per_file: int = 40,   # was 24
    max_chars_per_file: int = 1800, # was 1000
) -> list[dict]:
```

In `archon/agent.py`, modify the prefetch call in `_build_turn_system_prompt()`:

```python
prefetched = memory_store.prefetch_for_query(user_message, limit=4)
```

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add archon/memory.py archon/agent.py tests/test_compressor.py
git commit -m "feat: increase memory retrieval surface (4 results, 1800 chars)"
```

---

## Chunk 7: Session Distillation & Heartbeat

**Goal:** Add LLM-powered session distillation that extracts structured learnings at session end (all output goes through `inbox_add`), and a heartbeat runner for proactive behavior.

### Task 18: Session Distiller

**Files:**
- Create: `archon/memory/distiller.py`
- Test: `tests/test_distiller.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_distiller.py
"""Session distillation tests."""

from archon.memory.distiller import build_distillation_prompt, parse_distillation_output


def test_build_distillation_prompt():
    messages = [
        {"role": "user", "content": "set up browser-use"},
        {"role": "assistant", "content": "I installed the dependencies and configured chromium."},
    ]
    prompt = build_distillation_prompt(messages)
    assert "extract" in prompt.lower() or "analyze" in prompt.lower()


def test_parse_distillation_output_facts():
    llm_output = """
FACT|high|project:browser-use|browser-use requires chromium and OPENAI_API_KEY|projects/browser-use.md
PROCEDURE|high|project:browser-use|To run: source .venv/bin/activate && python script.py|projects/browser-use.md
CORRECTION|high|global|bun is preferred over npm for korami-site|projects/korami-site.md
GAP|medium|global|User wanted to send email via browser-use but it failed|capability_gaps.md
"""
    items = parse_distillation_output(llm_output)
    assert len(items) == 4
    assert items[0]["kind"] == "fact"
    assert items[0]["confidence"] == "high"
    assert items[1]["kind"] == "procedure"
    assert items[2]["kind"] == "correction"
    assert items[3]["kind"] == "gap"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_distiller.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement distiller**

```python
# archon/memory/distiller.py
"""LLM-powered session distillation — extract structured learnings from conversations."""

from __future__ import annotations


DISTILLATION_PROMPT = """\
Analyze this conversation and extract structured learnings. For each item, output one line in this format:
KIND|CONFIDENCE|SCOPE|SUMMARY|TARGET_PATH

Where:
- KIND: fact, procedure, correction, preference, gap
- CONFIDENCE: high, medium, low
- SCOPE: global, or project:<name>
- SUMMARY: one-line description
- TARGET_PATH: suggested memory file (e.g., projects/browser-use.md, user/preferences.md, capability_gaps.md)

Rules:
- Only extract facts that are CONFIRMED in the conversation, not speculated
- Procedures should describe step-by-step processes that WORKED
- Corrections are where the user said something was wrong
- Gaps are things the user wanted but the assistant could not do
- Preferences are explicit user preferences stated in the conversation
- If nothing useful to extract, output: NONE

Conversation:
"""


def build_distillation_prompt(messages: list[dict], max_chars: int = 12000) -> str:
    """Build the prompt for session distillation."""
    lines = [DISTILLATION_PROMPT]
    total = 0
    for msg in messages:
        role = str(msg.get("role", "unknown"))
        content = _flatten_for_distillation(msg.get("content"))
        if not content:
            continue
        entry = f"{role}: {content}"
        if total + len(entry) > max_chars:
            break
        lines.append(entry)
        total += len(entry)
    return "\n".join(lines)


def parse_distillation_output(llm_output: str) -> list[dict]:
    """Parse structured distillation output into inbox-ready dicts."""
    items: list[dict] = []
    for line in llm_output.strip().splitlines():
        line = line.strip()
        if not line or line == "NONE":
            continue
        parts = line.split("|", 4)
        if len(parts) < 4:
            continue
        kind = parts[0].strip().lower()
        confidence = parts[1].strip().lower()
        scope = parts[2].strip()
        summary = parts[3].strip()
        target_path = parts[4].strip() if len(parts) > 4 else ""

        if kind not in ("fact", "procedure", "correction", "preference", "gap"):
            continue
        if confidence not in ("high", "medium", "low"):
            confidence = "medium"

        items.append({
            "kind": kind,
            "confidence": confidence,
            "scope": scope,
            "summary": summary,
            "target_path": target_path,
            "source": "session_distillation",
        })
    return items


def _flatten_for_distillation(content: object, max_chars: int = 400) -> str:
    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                elif item.get("type") == "tool_use":
                    parts.append(f"[used tool: {item.get('name', '')}]")
        text = " ".join(parts).strip()
    else:
        text = str(content or "").strip()
    return text[:max_chars] if len(text) > max_chars else text
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_distiller.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add archon/memory/distiller.py tests/test_distiller.py
git commit -m "feat: add session distillation prompt builder and output parser"
```

### Task 19: Heartbeat Runner

**Files:**
- Create: `archon/heartbeat.py`
- Modify: `archon/config.py` (add `HEARTBEAT_PATH` if desired, or document `CONFIG_DIR / "heartbeat.md"`)
- Test: `tests/test_heartbeat.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_heartbeat.py
"""Heartbeat runner tests."""

from archon.heartbeat import parse_checklist, ChecklistItem, run_heartbeat, HEARTBEAT_OK


def test_parse_checklist():
    text = """# Heartbeat
- [ ] Check disk space
- [x] Already done task
- [ ] Check blocked setup jobs
"""
    items = parse_checklist(text)
    assert len(items) == 3
    active = [i for i in items if not i.checked]
    assert len(active) == 2
    assert active[0].text == "Check disk space"


def test_parse_empty():
    items = parse_checklist("")
    assert items == []


def test_parse_no_checkboxes():
    items = parse_checklist("# Just a heading\nSome text.\n")
    assert items == []


def test_run_heartbeat_only_notifies_actionable_items():
    class FakeAgent:
        def __init__(self, replies):
            self._replies = list(replies)
        def run(self, prompt, policy_profile=None):
            return self._replies.pop(0)

    sent = []
    items = [
        ChecklistItem(text="Check disk space", checked=False, line_number=1),
        ChecklistItem(text="Check blocked setup jobs", checked=False, line_number=2),
    ]

    run_heartbeat(
        items=items,
        agent_factory=lambda: FakeAgent([HEARTBEAT_OK, "Disk low on /home"]),
        notify_fn=sent.append,
    )
    assert len(sent) == 1
    assert "Disk low on /home" in sent[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_heartbeat.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement heartbeat**

```python
# archon/heartbeat.py
"""Heartbeat runner — reads a checklist and runs proactive checks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from archon.config import CONFIG_DIR


HEARTBEAT_PATH = CONFIG_DIR / "heartbeat.md"
HEARTBEAT_OK = "HEARTBEAT_OK"

_CHECKBOX_RE = re.compile(r"^-\s+\[([ xX])\]\s+(.+)$")


@dataclass
class ChecklistItem:
    text: str
    checked: bool
    line_number: int


def parse_checklist(text: str) -> list[ChecklistItem]:
    """Parse markdown checkbox items from a heartbeat file."""
    items: list[ChecklistItem] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        match = _CHECKBOX_RE.match(line.strip())
        if match:
            checked = match.group(1).lower() == "x"
            items.append(ChecklistItem(
                text=match.group(2).strip(),
                checked=checked,
                line_number=lineno,
            ))
    return items


def load_heartbeat_items() -> list[ChecklistItem]:
    """Load active (unchecked) items from the heartbeat file."""
    if not HEARTBEAT_PATH.exists():
        return []
    text = HEARTBEAT_PATH.read_text(errors="replace")
    items = parse_checklist(text)
    return [i for i in items if not i.checked]


def build_heartbeat_prompt(item: ChecklistItem) -> str:
    """Build the agent prompt for a single heartbeat check."""
    return (
        f"Heartbeat check: {item.text}\n\n"
        f"If nothing needs attention, respond exactly: {HEARTBEAT_OK}\n"
        f"If action is needed, take it and report what you did."
    )


def run_heartbeat(
    *,
    items: list[ChecklistItem] | None = None,
    agent_factory,
    notify_fn,
    policy_profile: str = "heartbeat",
) -> None:
    """Run proactive checks and notify only for actionable results."""
    active_items = items if items is not None else load_heartbeat_items()
    for item in active_items:
        agent = agent_factory()
        result = str(agent.run(build_heartbeat_prompt(item), policy_profile=policy_profile) or "").strip()
        if result == HEARTBEAT_OK or not result:
            continue
        notify_fn(f"Heartbeat: {item.text}\n{result}")
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_heartbeat.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add archon/heartbeat.py tests/test_heartbeat.py
git commit -m "feat: add heartbeat checklist parser and runner"
```

---

## Chunk 8: Skill Auto-Generation

**Goal:** After a successful multi-step session involving a project, offer to generate a SKILL.md that captures the learned procedures.

### Task 20: Skill Generator

**Files:**
- Create: `archon/skills/generator.py`
- Test: `tests/test_skill_generator.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_skill_generator.py
"""SKILL.md generation tests."""

from archon.skills.generator import build_skill_generation_prompt, write_skill_folder
from pathlib import Path


def test_build_skill_generation_prompt():
    project_name = "browser-use"
    procedures = [
        "1. source .venv/bin/activate",
        "2. python script.py",
    ]
    prompt = build_skill_generation_prompt(project_name, procedures)
    assert "browser-use" in prompt
    assert "SKILL.md" in prompt or "skill" in prompt.lower()


def test_write_skill_folder(tmp_path):
    skill_content = """---
name: test-skill
description: A test
triggers:
  - do test
---
## Steps
1. Run test
"""
    path = write_skill_folder(tmp_path, "test-skill", skill_content)
    assert (tmp_path / "test-skill" / "SKILL.md").exists()
    assert "test-skill" in str(path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_skill_generator.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement skill generator**

```python
# archon/skills/generator.py
"""Auto-generate SKILL.md from successful sessions."""

from __future__ import annotations

from pathlib import Path


SKILL_GENERATION_PROMPT = """\
Generate a SKILL.md file for the project "{project_name}".

Based on the procedures that worked during this session:
{procedures}

The SKILL.md should follow this format:
---
name: {project_name}
description: <one-line description>
triggers:
  - <natural trigger phrase 1>
  - <natural trigger phrase 2>
requires:
  bins: [<required binaries>]
  env: [<required env vars>]
tools: [shell, read_file, write_file]
---

## Steps
<numbered steps that worked>

## Known Issues
<any issues encountered and their solutions>

## Error Recovery
<what to do when common errors occur>

Output ONLY the SKILL.md content, nothing else.
"""


def build_skill_generation_prompt(
    project_name: str,
    procedures: list[str],
    known_issues: list[str] | None = None,
) -> str:
    proc_text = "\n".join(procedures) if procedures else "(none recorded)"
    prompt = SKILL_GENERATION_PROMPT.format(
        project_name=project_name,
        procedures=proc_text,
    )
    if known_issues:
        prompt += f"\n\nKnown issues encountered:\n" + "\n".join(known_issues)
    return prompt


def write_skill_folder(
    skills_dir: Path,
    skill_name: str,
    skill_content: str,
) -> Path:
    """Write a SKILL.md file to a skill folder."""
    folder = skills_dir / skill_name
    folder.mkdir(parents=True, exist_ok=True)
    skill_file = folder / "SKILL.md"
    skill_file.write_text(skill_content)
    return skill_file
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_skill_generator.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add archon/skills/generator.py tests/test_skill_generator.py
git commit -m "feat: add SKILL.md auto-generation from session procedures"
```

---

## Integration Notes

### How the Pieces Connect

1. **User says "learn how to use browser-use"**
   → `learn_project` tool fires → `scanner.scan_project()` → `assessor.assess_capabilities()`
   → Creates `SetupRecord` with steps → Executes archon steps via SmartLoop
   → Hits human requirement → `ask_human` tool → structured suspension request → setup job goes `blocked`
   → Notified via terminal/Telegram

2. **User says "here's the API key: sk-abc"**
   → `session_controller` detects potential resume → `resume.match_input_to_blocked_job()`
   → Exactly one plausible blocked setup job matches → mark step provided → resume setup via fresh turn
   → Remaining steps execute → verification → `generator.write_skill_folder()`
   → SKILL.md saved → skill available next session

3. **Next session: "use browser-use to check gmail"**
   → markdown skill trigger matches the request → session profile activates for `browser-use`
   → Instructions injected via `build_skill_guidance()` and enforced through the normal profile/tool-scope path
   → Archon follows the learned procedure in 2-3 steps

4. **Session ends**
   → `distiller.build_distillation_prompt()` → LLM extracts facts/procedures/corrections
   → All items queued via `memory.inbox_add()` → User reviews via `/memory inbox`

5. **Heartbeat fires (cron, every 30 min)**
   → `heartbeat.load_heartbeat_items()` → runs agent per item
   → Checks blocked jobs, disk space, project health → notifies if action needed

### Config Example (archon.toml)

```toml
[smart_loop]
enabled = true
max_steps = 20
wall_timeout_sec = 600
tool_timeout_sec = 60
turn_budget_tokens = 100000
max_consecutive_errors = 3
```

### Rollout Strategy

1. **SmartLoop behind config gate** (`smart_loop.enabled = false` default) — safe to merge, opt-in
2. **Setup backend** — purely additive, no existing code changes
3. **ask_human + suspension** — additive tool, SmartLoop handles signal
4. **Markdown skills** — loads alongside existing BUILTIN_SKILLS, no conflict
5. **Memory improvements** — increased defaults, backward compatible
6. **Distillation + heartbeat** — fully additive, opt-in

Each chunk can be merged and tested independently. No chunk requires a later chunk to work.
