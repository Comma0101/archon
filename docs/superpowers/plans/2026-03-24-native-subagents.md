# Native Subagent System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `spawn_subagent` tool that runs lightweight agent loops in-process with fresh context windows, model tiering, and restricted tool sets.

**Architecture:** A `SubagentRunner` implements a simplified agent loop (LLM call → tool execution → repeat). A `build_subagent_registry()` function builds fresh `ToolRegistry` instances per subagent type so handler closures bind correctly. A `[llm.tiers]` config section enables model routing.

**Tech Stack:** Python, existing `LLMClient`, existing `ToolRegistry`, existing `evaluate_tool_policy()`.

**Spec:** `docs/superpowers/specs/2026-03-24-native-subagents-design.md`

---

### Task 1: TierConfig dataclass and config parsing

**Files:**
- Modify: `archon/config.py:31-40` (near LLMConfig), `archon/config.py:212-227` (Config dataclass), `archon/config.py:239-249` (load_config llm section)
- Test: `tests/test_subagent_tier_config.py`

- [ ] **Step 1: Write failing tests for TierConfig**

```python
# tests/test_subagent_tier_config.py
"""Tests for model tier configuration."""
from archon.config import Config, TierConfig, load_config


def test_tier_config_defaults():
    cfg = TierConfig()
    assert cfg.light == ""
    assert cfg.standard == ""


def test_config_has_tiers_field():
    cfg = Config()
    assert isinstance(cfg.tiers, TierConfig)
    assert cfg.tiers.light == ""
    assert cfg.tiers.standard == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_subagent_tier_config.py -v`
Expected: FAIL with `AttributeError: 'Config' has no attribute 'tiers'` or similar

- [ ] **Step 3: Add TierConfig dataclass to config.py**

In `archon/config.py`, after the `LLMConfig` dataclass (around line 40), add:

```python
@dataclass
class TierConfig:
    light: str = ""
    standard: str = ""
```

In the `Config` dataclass (around line 227), add the field:

```python
    tiers: TierConfig = field(default_factory=TierConfig)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_subagent_tier_config.py -v`
Expected: PASS

- [ ] **Step 5: Write failing tests for TOML parsing and resolve_tier_model**

Append to `tests/test_subagent_tier_config.py`:

```python
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from archon.config import resolve_tier_model


def test_load_config_parses_tiers(tmp_path):
    toml_content = b'[llm.tiers]\nlight = "custom-haiku"\nstandard = "custom-sonnet"\n'
    config_dir = tmp_path / ".config" / "archon"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_bytes(toml_content)
    with patch("archon.config.CONFIG_DIR", config_dir):
        cfg = load_config()
    assert cfg.tiers.light == "custom-haiku"
    assert cfg.tiers.standard == "custom-sonnet"


def test_resolve_tier_model_light_auto_anthropic():
    cfg = Config()
    cfg.llm.provider = "anthropic"
    result = resolve_tier_model(cfg, "light")
    assert result == "claude-haiku-4-5-20251001"


def test_resolve_tier_model_light_auto_openai():
    cfg = Config()
    cfg.llm.provider = "openai"
    result = resolve_tier_model(cfg, "light")
    assert result == "gpt-4o-mini"


def test_resolve_tier_model_light_auto_google():
    cfg = Config()
    cfg.llm.provider = "google"
    result = resolve_tier_model(cfg, "light")
    assert result == "gemini-2.5-flash"


def test_resolve_tier_model_standard_inherits_main():
    cfg = Config()
    cfg.llm.model = "my-custom-model"
    result = resolve_tier_model(cfg, "standard")
    assert result == "my-custom-model"


def test_resolve_tier_model_user_override():
    cfg = Config()
    cfg.tiers.light = "my-light-model"
    result = resolve_tier_model(cfg, "light")
    assert result == "my-light-model"


def test_resolve_tier_model_unknown_tier():
    cfg = Config()
    result = resolve_tier_model(cfg, "unknown")
    assert result == cfg.llm.model
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `pytest tests/test_subagent_tier_config.py -v`
Expected: FAIL — `resolve_tier_model` does not exist, TOML parsing doesn't handle `[llm.tiers]`

- [ ] **Step 7: Implement TOML parsing and resolve_tier_model**

In `archon/config.py` `load_config()`, after the `fallback` parsing block (around line 249), add:

```python
        tiers = llm.get("tiers", {})
        cfg.tiers.light = str(tiers.get("light", cfg.tiers.light) or "").strip()
        cfg.tiers.standard = str(tiers.get("standard", cfg.tiers.standard) or "").strip()
```

After the `TierConfig` dataclass, add:

```python
_LIGHT_MODEL_DEFAULTS = {
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "gpt-4o-mini",
    "google": "gemini-2.5-flash",
}


def resolve_tier_model(config: "Config", tier: str) -> str:
    """Return the model string for a tier, applying auto-detection if empty."""
    tier_key = (tier or "").strip().lower()
    tiers = getattr(config, "tiers", None) or TierConfig()

    if tier_key == "light":
        if tiers.light.strip():
            return tiers.light.strip()
        provider = str(getattr(config.llm, "provider", "") or "").strip().lower()
        return _LIGHT_MODEL_DEFAULTS.get(provider, config.llm.model)

    if tier_key == "standard":
        if tiers.standard.strip():
            return tiers.standard.strip()
        return config.llm.model

    return config.llm.model
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/test_subagent_tier_config.py -v`
Expected: ALL PASS

- [ ] **Step 9: Run full test suite for regressions**

Run: `pytest tests/ -x -q`
Expected: All existing tests pass

- [ ] **Step 10: Commit**

```bash
git add archon/config.py tests/test_subagent_tier_config.py
git commit -m "feat: add TierConfig and resolve_tier_model for model tier routing"
```

---

### Task 2: Subagent type definitions

**Files:**
- Create: `archon/subagents/__init__.py`
- Create: `archon/subagents/types.py`
- Test: `tests/test_subagent_types.py`

- [ ] **Step 1: Write failing tests for type definitions**

```python
# tests/test_subagent_types.py
"""Tests for subagent type definitions."""
from archon.subagents.types import SUBAGENT_TYPES, get_subagent_type


def test_explore_type_exists():
    t = get_subagent_type("explore")
    assert t is not None
    assert t["tier"] == "light"
    assert t["max_iterations"] == 8
    assert t["wall_clock_timeout_sec"] == 60
    assert t["max_result_chars"] == 3000
    assert "system_prompt" in t
    assert len(t["system_prompt"]) > 0


def test_general_type_exists():
    t = get_subagent_type("general")
    assert t is not None
    assert t["tier"] == "standard"
    assert t["max_iterations"] == 12
    assert t["wall_clock_timeout_sec"] == 300
    assert t["max_result_chars"] == 3000


def test_unknown_type_returns_none():
    assert get_subagent_type("bogus") is None


def test_explore_registration_functions():
    t = get_subagent_type("explore")
    funcs = t["register_functions"]
    assert "register_filesystem_tools" in funcs
    assert "register_memory_tools" not in funcs
    assert "register_worker_tools" not in funcs


def test_general_registration_functions():
    t = get_subagent_type("general")
    funcs = t["register_functions"]
    assert "register_filesystem_tools" in funcs
    assert "register_memory_tools" in funcs
    assert "register_content_tools" in funcs
    assert "register_worker_tools" not in funcs
    # spawn_subagent excluded by not calling register_subagent_tools
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_subagent_types.py -v`
Expected: FAIL — `archon.subagents` doesn't exist

- [ ] **Step 3: Create the subagents package and types module**

```python
# archon/subagents/__init__.py
"""Native subagent system."""
```

```python
# archon/subagents/types.py
"""Subagent type definitions: tool allowlists, system prompts, defaults."""

EXPLORE_SYSTEM_PROMPT = (
    "You are a codebase search assistant. Find the requested information "
    "efficiently using read_file, grep, glob, and shell (read-only commands "
    "only). Return a concise summary of what you found with exact file paths "
    "and line numbers. Do not modify any files."
)

GENERAL_SYSTEM_PROMPT = (
    "You are a task execution assistant. Complete the requested task "
    "thoroughly. Return a summary of what you did, what changed, and any "
    "issues encountered."
)

SUBAGENT_TYPES: dict[str, dict] = {
    "explore": {
        "tier": "light",
        "max_iterations": 8,
        "wall_clock_timeout_sec": 60,
        "max_result_chars": 3000,
        "system_prompt": EXPLORE_SYSTEM_PROMPT,
        "register_functions": ["register_filesystem_tools"],
    },
    "general": {
        "tier": "standard",
        "max_iterations": 12,
        "wall_clock_timeout_sec": 300,
        "max_result_chars": 3000,
        "system_prompt": GENERAL_SYSTEM_PROMPT,
        "register_functions": [
            "register_filesystem_tools",
            "register_memory_tools",
            "register_content_tools",
            "register_mcp_tools",
            "register_setup_tools",
            "register_call_service_tools",
            "register_call_mission_tools",
        ],
    },
}


def get_subagent_type(name: str) -> dict | None:
    """Return type definition or None if unknown."""
    return SUBAGENT_TYPES.get((name or "").strip().lower())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_subagent_types.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add archon/subagents/__init__.py archon/subagents/types.py tests/test_subagent_types.py
git commit -m "feat: add subagent type definitions (explore, general)"
```

---

### Task 3: Fresh registry builder

**Files:**
- Create: `archon/subagents/registry.py`
- Test: `tests/test_subagent_registry.py`

- [ ] **Step 1: Write failing tests for build_subagent_registry**

```python
# tests/test_subagent_registry.py
"""Tests for subagent registry builder."""
from archon.config import Config
from archon.safety import Level
from archon.subagents.registry import build_subagent_registry


def _always_confirm(label, level):
    return True


def test_explore_registry_has_read_only_tools():
    cfg = Config()
    reg = build_subagent_registry(cfg, "explore", confirmer=_always_confirm)
    names = set(reg.tools.keys())
    assert "shell" in names
    assert "read_file" in names
    assert "grep" in names
    assert "glob" in names
    # Must not have write tools
    assert "write_file" not in names
    assert "edit_file" not in names
    # Must not have spawn_subagent
    assert "spawn_subagent" not in names
    # Must not have worker tools
    assert "delegate_code_task" not in names


def test_explore_registry_rejects_dangerous_shell():
    """Explore confirmer must reject DANGEROUS commands."""
    calls = []

    def tracking_confirmer(label, level):
        calls.append((label, level))
        return True  # parent would allow it

    cfg = Config()
    reg = build_subagent_registry(cfg, "explore", confirmer=tracking_confirmer)
    # The explore registry's confirmer should wrap to reject non-SAFE
    # Execute a shell command classified as dangerous
    result = reg.execute("shell", {"command": "rm -rf /tmp/test"})
    assert "rejected" in result.lower() or "safety" in result.lower()


def test_general_registry_has_filesystem_and_memory_tools():
    cfg = Config()
    reg = build_subagent_registry(cfg, "general", confirmer=_always_confirm)
    names = set(reg.tools.keys())
    assert "shell" in names
    assert "read_file" in names
    assert "write_file" in names
    # Must not have spawn_subagent
    assert "spawn_subagent" not in names
    # Must not have worker tools
    assert "delegate_code_task" not in names
    assert "worker_start" not in names


def test_general_registry_uses_parent_confirmer():
    """General registry uses the parent confirmer unchanged."""
    confirm_calls = []

    def tracking_confirmer(label, level):
        confirm_calls.append(level)
        return True

    cfg = Config()
    reg = build_subagent_registry(cfg, "general", confirmer=tracking_confirmer)
    # The confirmer should be the parent's, not wrapped
    reg.execute("shell", {"command": "echo hello"})
    # Should have been called with the actual safety level (SAFE for echo)
    assert len(confirm_calls) > 0


def test_unknown_type_raises():
    cfg = Config()
    try:
        build_subagent_registry(cfg, "bogus", confirmer=_always_confirm)
        assert False, "Should have raised ValueError"
    except ValueError:
        pass
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_subagent_registry.py -v`
Expected: FAIL — `archon.subagents.registry` doesn't exist

- [ ] **Step 3: Implement build_subagent_registry**

```python
# archon/subagents/registry.py
"""Build fresh ToolRegistry instances for subagents.

Tool handlers are closures over their registry, so we must build a
new registry and re-register tools — NOT copy handlers from the parent.
"""
from __future__ import annotations

from typing import Callable

from archon.config import Config
from archon.safety import Level
from archon.subagents.types import get_subagent_type
from archon.tools import ToolRegistry
from archon.tooling import (
    register_call_mission_tools,
    register_call_service_tools,
    register_content_tools,
    register_filesystem_tools,
    register_memory_tools,
    register_mcp_tools,
    register_setup_tools,
)

_REGISTER_MAP: dict[str, Callable] = {
    "register_filesystem_tools": register_filesystem_tools,
    "register_memory_tools": register_memory_tools,
    "register_content_tools": register_content_tools,
    "register_mcp_tools": register_mcp_tools,
    "register_setup_tools": register_setup_tools,
    "register_call_service_tools": register_call_service_tools,
    "register_call_mission_tools": register_call_mission_tools,
}


def build_subagent_registry(
    parent_config: Config,
    subagent_type: str,
    confirmer: Callable[[str, Level], bool] | None = None,
    archon_source_dir: str | None = None,
) -> ToolRegistry:
    """Build a fresh ToolRegistry for a subagent.

    Calls registration functions directly so handler closures bind to
    the new registry's confirmer and config, not the parent's.
    """
    type_def = get_subagent_type(subagent_type)
    if type_def is None:
        raise ValueError(f"Unknown subagent type: {subagent_type!r}")

    effective_confirmer = confirmer or (lambda _label, _level: True)
    if subagent_type == "explore":
        effective_confirmer = _wrap_explore_confirmer(effective_confirmer, archon_source_dir)

    # Build a fresh registry — do NOT call _register_builtins()
    registry = ToolRegistry.__new__(ToolRegistry)
    registry.tools = {}
    registry.handlers = {}
    registry.archon_source_dir = archon_source_dir
    registry.confirmer = effective_confirmer
    registry.config = parent_config
    registry.mcp_client_cls = None
    registry._execute_event_handler = None
    registry._worker_session_affinity = {}
    registry._session_id = ""

    for func_name in type_def["register_functions"]:
        register_fn = _REGISTER_MAP.get(func_name)
        if register_fn is not None:
            register_fn(registry)

    return registry


def _wrap_explore_confirmer(
    parent_confirmer: Callable[[str, Level], bool],
    archon_source_dir: str | None,
) -> Callable[[str, Level], bool]:
    """Wrap confirmer to reject DANGEROUS/FORBIDDEN for explore subagents."""

    def _explore_confirmer(label: str, level: Level) -> bool:
        if level in (Level.DANGEROUS, Level.FORBIDDEN):
            return False
        return parent_confirmer(label, level)

    return _explore_confirmer
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_subagent_registry.py -v`
Expected: ALL PASS

- [ ] **Step 5: Run full test suite for regressions**

Run: `pytest tests/ -x -q`
Expected: All existing tests pass

- [ ] **Step 6: Commit**

```bash
git add archon/subagents/registry.py tests/test_subagent_registry.py
git commit -m "feat: add build_subagent_registry with fresh tool registration"
```

---

### Task 4: SubagentRunner — dataclasses and the iteration loop

**Files:**
- Create: `archon/subagents/runner.py`
- Test: `tests/test_subagent_runner.py`

- [ ] **Step 1: Write failing tests for SubagentRunner**

```python
# tests/test_subagent_runner.py
"""Tests for SubagentRunner."""
from unittest.mock import MagicMock, patch

from archon.llm import LLMResponse, ToolCall
from archon.subagents.runner import SubagentConfig, SubagentResult, SubagentRunner


def _make_text_response(text, input_tokens=100, output_tokens=50):
    return LLMResponse(
        text=text,
        tool_calls=[],
        raw_content=[{"type": "text", "text": text}],
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _make_tool_response(tool_name, tool_args, input_tokens=100, output_tokens=50):
    tc = ToolCall(id="tc_001", name=tool_name, arguments=tool_args)
    return LLMResponse(
        text=None,
        tool_calls=[tc],
        raw_content=[{"type": "tool_use", "id": "tc_001", "name": tool_name, "input": tool_args}],
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _make_config(**overrides):
    defaults = dict(
        type="explore",
        task="find config files",
        context="",
        max_iterations=3,
        max_result_chars=3000,
        model="test-model",
        system_prompt="You are a test assistant.",
        wall_clock_timeout_sec=60.0,
    )
    defaults.update(overrides)
    return SubagentConfig(**defaults)


def test_simple_text_response():
    """LLM returns text immediately — no tool calls."""
    llm = MagicMock()
    llm.chat.return_value = _make_text_response("Found 3 config files.")
    tools = MagicMock()
    tools.get_schemas.return_value = []
    config = _make_config()
    runner = SubagentRunner(config=config, llm=llm, tools=tools)
    result = runner.run()
    assert result.status == "ok"
    assert "Found 3 config files" in result.text
    assert result.input_tokens == 100
    assert result.output_tokens == 50
    assert result.iterations_used == 1


def test_tool_call_then_text():
    """LLM calls a tool, then responds with text."""
    llm = MagicMock()
    llm.chat.side_effect = [
        _make_tool_response("grep", {"pattern": "config", "path": "."}),
        _make_text_response("Found config in 2 files."),
    ]
    tools = MagicMock()
    tools.get_schemas.return_value = [{"name": "grep"}]
    tools.execute.return_value = "archon/config.py\narchon/cli.py"
    config = _make_config()
    runner = SubagentRunner(config=config, llm=llm, tools=tools)
    result = runner.run()
    assert result.status == "ok"
    assert result.iterations_used == 2
    assert result.input_tokens == 200
    assert result.output_tokens == 100


def test_iteration_limit():
    """Runner hits max_iterations."""
    llm = MagicMock()
    llm.chat.return_value = _make_tool_response("grep", {"pattern": "x"})
    tools = MagicMock()
    tools.get_schemas.return_value = [{"name": "grep"}]
    tools.execute.return_value = "some result"
    config = _make_config(max_iterations=2)
    runner = SubagentRunner(config=config, llm=llm, tools=tools)
    result = runner.run()
    assert result.status == "iteration_limit"
    assert result.iterations_used == 2


def test_llm_failure():
    """LLM raises an exception."""
    llm = MagicMock()
    llm.chat.side_effect = RuntimeError("API timeout")
    tools = MagicMock()
    tools.get_schemas.return_value = []
    config = _make_config()
    runner = SubagentRunner(config=config, llm=llm, tools=tools)
    result = runner.run()
    assert result.status == "failed"
    assert "API timeout" in result.text


def test_consecutive_tool_errors():
    """Runner stops after 3 consecutive tool errors."""
    llm = MagicMock()
    llm.chat.return_value = _make_tool_response("shell", {"command": "bad"})
    tools = MagicMock()
    tools.get_schemas.return_value = [{"name": "shell"}]
    tools.execute.return_value = "Error: command failed"
    config = _make_config(max_iterations=10)
    runner = SubagentRunner(config=config, llm=llm, tools=tools)
    result = runner.run()
    assert result.status == "failed"
    assert result.iterations_used <= 4  # should stop early


def test_result_truncation():
    """Result text is truncated to max_result_chars."""
    llm = MagicMock()
    llm.chat.return_value = _make_text_response("x" * 5000)
    tools = MagicMock()
    tools.get_schemas.return_value = []
    config = _make_config(max_result_chars=100)
    runner = SubagentRunner(config=config, llm=llm, tools=tools)
    result = runner.run()
    assert len(result.text) <= 100


def test_suspension_request_treated_as_error():
    """SuspensionRequest from tool.execute is treated as failure."""
    from archon.execution.contracts import SuspensionRequest

    llm = MagicMock()
    llm.chat.return_value = _make_tool_response("delegate_code_task", {"task": "x"})
    tools = MagicMock()
    tools.get_schemas.return_value = [{"name": "delegate_code_task"}]
    tools.execute.return_value = SuspensionRequest(job_id="j1", message="waiting")
    config = _make_config()
    runner = SubagentRunner(config=config, llm=llm, tools=tools)
    result = runner.run()
    assert result.status == "failed"
    assert "suspension" in result.text.lower() or "suspend" in result.text.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_subagent_runner.py -v`
Expected: FAIL — `archon.subagents.runner` doesn't exist

- [ ] **Step 3: Implement SubagentRunner**

```python
# archon/subagents/runner.py
"""Lightweight subagent execution loop."""
from __future__ import annotations

import time
from dataclasses import dataclass

from archon.execution.contracts import SuspensionRequest
from archon.llm import LLMClient, LLMResponse
from archon.security.redaction import redact_secret_like_text
from archon.tools import ToolRegistry


@dataclass
class SubagentConfig:
    type: str
    task: str
    context: str = ""
    max_iterations: int = 8
    max_result_chars: int = 3000
    model: str = ""
    system_prompt: str = ""
    wall_clock_timeout_sec: float = 60.0


@dataclass
class SubagentResult:
    status: str  # ok | failed | timeout | iteration_limit
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    iterations_used: int = 0


class SubagentRunner:
    """Run a simplified agent loop with a fresh context window."""

    _MAX_CONSECUTIVE_TOOL_ERRORS = 3

    def __init__(
        self,
        config: SubagentConfig,
        llm: LLMClient,
        tools: ToolRegistry,
    ):
        self.config = config
        self.llm = llm
        self.tools = tools

    def run(self) -> SubagentResult:
        prompt = self.config.task
        if self.config.context:
            prompt = f"{prompt}\n\nContext:\n{self.config.context}"

        history: list[dict] = [{"role": "user", "content": prompt}]
        tool_schemas = self.tools.get_schemas()
        total_in = 0
        total_out = 0
        consecutive_errors = 0
        last_text = ""
        started_at = time.monotonic()

        for iteration in range(1, self.config.max_iterations + 1):
            if time.monotonic() - started_at > self.config.wall_clock_timeout_sec:
                return SubagentResult(
                    status="timeout",
                    text=self._truncate(last_text or "Timed out."),
                    input_tokens=total_in,
                    output_tokens=total_out,
                    iterations_used=iteration,
                )

            try:
                response = self.llm.chat(
                    self.config.system_prompt,
                    history,
                    tools=tool_schemas,
                )
            except Exception as e:
                return SubagentResult(
                    status="failed",
                    text=self._truncate(f"LLM error: {type(e).__name__}: {e}"),
                    input_tokens=total_in,
                    output_tokens=total_out,
                    iterations_used=iteration,
                )

            total_in += response.input_tokens
            total_out += response.output_tokens

            if not response.tool_calls:
                last_text = response.text or ""
                return SubagentResult(
                    status="ok",
                    text=self._truncate(last_text),
                    input_tokens=total_in,
                    output_tokens=total_out,
                    iterations_used=iteration,
                )

            # Append assistant message with tool calls
            history.append({"role": "assistant", "content": response.raw_content})

            # Execute tools
            tool_results = []
            for call in response.tool_calls:
                raw_result = self.tools.execute(call.name, call.arguments)

                if isinstance(raw_result, SuspensionRequest):
                    return SubagentResult(
                        status="failed",
                        text=self._truncate(
                            f"Subagents do not support suspension (tool={call.name})."
                        ),
                        input_tokens=total_in,
                        output_tokens=total_out,
                        iterations_used=iteration,
                    )

                result_text = redact_secret_like_text(str(raw_result))
                is_error = result_text.startswith("Error:")
                if is_error:
                    consecutive_errors += 1
                else:
                    consecutive_errors = 0

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": result_text,
                })

            history.append({"role": "user", "content": tool_results})

            if consecutive_errors >= self._MAX_CONSECUTIVE_TOOL_ERRORS:
                return SubagentResult(
                    status="failed",
                    text=self._truncate(
                        "Stopped after repeated tool errors."
                    ),
                    input_tokens=total_in,
                    output_tokens=total_out,
                    iterations_used=iteration,
                )

        # Exhausted all iterations without a text response
        return SubagentResult(
            status="iteration_limit",
            text=self._truncate(last_text or "Reached iteration limit."),
            input_tokens=total_in,
            output_tokens=total_out,
            iterations_used=self.config.max_iterations,
        )

    def _truncate(self, text: str) -> str:
        limit = self.config.max_result_chars
        if len(text) <= limit:
            return text
        return text[:limit]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_subagent_runner.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add archon/subagents/runner.py tests/test_subagent_runner.py
git commit -m "feat: add SubagentRunner with simplified agent loop"
```

---

### Task 5: spawn_subagent tool registration

**Files:**
- Create: `archon/subagents/tools.py`
- Test: `tests/test_subagent_tools.py`

- [ ] **Step 1: Write failing tests for spawn_subagent tool**

```python
# tests/test_subagent_tools.py
"""Tests for spawn_subagent tool registration and handler."""
from unittest.mock import MagicMock, patch

from archon.config import Config
from archon.safety import Level
from archon.subagents.tools import register_subagent_tools
from archon.tools import ToolRegistry


def _make_registry():
    cfg = Config()
    reg = ToolRegistry(config=cfg, confirmer=lambda l, lv: True)
    return reg


def test_register_adds_spawn_subagent():
    reg = _make_registry()
    register_subagent_tools(reg)
    assert "spawn_subagent" in reg.tools
    assert "spawn_subagent" in reg.handlers


def test_spawn_subagent_invalid_type():
    reg = _make_registry()
    register_subagent_tools(reg)
    result = reg.execute("spawn_subagent", {"task": "do stuff", "type": "bogus"})
    assert "error" in result.lower() or "unknown" in result.lower()


def test_spawn_subagent_empty_task():
    reg = _make_registry()
    register_subagent_tools(reg)
    result = reg.execute("spawn_subagent", {"task": "", "type": "explore"})
    assert "error" in result.lower() or "empty" in result.lower()


@patch("archon.subagents.tools.SubagentRunner")
@patch("archon.subagents.tools.LLMClient")
@patch("archon.subagents.tools.build_subagent_registry")
def test_spawn_subagent_explore_runs_runner(mock_build_reg, mock_llm_cls, mock_runner_cls):
    from archon.subagents.runner import SubagentResult

    mock_runner_instance = MagicMock()
    mock_runner_instance.run.return_value = SubagentResult(
        status="ok",
        text="Found 3 files.",
        input_tokens=500,
        output_tokens=200,
        iterations_used=2,
    )
    mock_runner_cls.return_value = mock_runner_instance
    mock_build_reg.return_value = MagicMock()

    reg = _make_registry()
    register_subagent_tools(reg)
    result = reg.execute("spawn_subagent", {"task": "find config files", "type": "explore"})
    assert "ok" in result
    assert "Found 3 files" in result
    assert mock_runner_cls.called
    assert mock_runner_instance.run.called


def test_spawn_subagent_result_format():
    """Verify result string includes status, iterations, tokens, and text."""
    from archon.subagents.tools import _format_subagent_result
    from archon.subagents.runner import SubagentResult

    result = SubagentResult(
        status="ok", text="Done.", input_tokens=100, output_tokens=50, iterations_used=3,
    )
    formatted = _format_subagent_result("explore", result, max_iterations=8)
    assert "subagent_type: explore" in formatted
    assert "status: ok" in formatted
    assert "iterations: 3/8" in formatted
    assert "tokens: 100 in, 50 out" in formatted
    assert "Done." in formatted
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_subagent_tools.py -v`
Expected: FAIL — `archon.subagents.tools` doesn't exist

- [ ] **Step 3: Implement register_subagent_tools**

```python
# archon/subagents/tools.py
"""Register the spawn_subagent tool on a ToolRegistry."""
from __future__ import annotations

from archon.config import resolve_tier_model
from archon.llm import LLMClient
from archon.subagents.registry import build_subagent_registry
from archon.subagents.runner import SubagentConfig, SubagentResult, SubagentRunner
from archon.subagents.types import get_subagent_type


def register_subagent_tools(registry) -> None:
    """Register spawn_subagent on the given ToolRegistry."""

    def spawn_subagent(task: str, type: str = "explore", context: str = "") -> str:
        task_text = (task or "").strip()
        if not task_text:
            return "Error: task is required and cannot be empty."

        type_key = (type or "explore").strip().lower()
        type_def = get_subagent_type(type_key)
        if type_def is None:
            valid = ", ".join(sorted(get_subagent_type.__module__ and ["explore", "general"]))
            return f"Error: unknown subagent type {type_key!r}. Valid types: explore, general."

        config = getattr(registry, "config", None)
        if config is None:
            return "Error: registry has no config — cannot resolve model tier."

        llm_config = getattr(config, "llm", None)
        model = resolve_tier_model(config, type_def["tier"])
        provider = str(getattr(llm_config, "provider", "anthropic") or "anthropic").strip()
        api_key = str(getattr(llm_config, "api_key", "") or "").strip()
        base_url = str(getattr(llm_config, "base_url", "") or "").strip()

        try:
            llm = LLMClient(
                provider=provider,
                model=model,
                api_key=api_key,
                base_url=base_url,
            )
        except Exception as e:
            return f"Error: failed to create LLM client for subagent: {type(e).__name__}: {e}"

        try:
            sub_registry = build_subagent_registry(
                parent_config=config,
                subagent_type=type_key,
                confirmer=registry.confirmer,
                archon_source_dir=getattr(registry, "archon_source_dir", None),
            )
        except Exception as e:
            return f"Error: failed to build subagent registry: {type(e).__name__}: {e}"

        sub_config = SubagentConfig(
            type=type_key,
            task=task_text,
            context=(context or "").strip(),
            max_iterations=type_def["max_iterations"],
            max_result_chars=type_def["max_result_chars"],
            model=model,
            system_prompt=type_def["system_prompt"],
            wall_clock_timeout_sec=type_def["wall_clock_timeout_sec"],
        )

        runner = SubagentRunner(config=sub_config, llm=llm, tools=sub_registry)
        result = runner.run()

        return _format_subagent_result(type_key, result, max_iterations=type_def["max_iterations"])

    registry.register(
        "spawn_subagent",
        (
            "Spawn a native subagent with its own context window to perform a task. "
            "type='explore' (default) uses a fast cheap model with read-only tools for "
            "codebase search. type='general' uses the main model with full tools (minus "
            "nesting) for implementation tasks. Prefer explore for search/analysis and "
            "general for code changes. For heavy sandboxed work, use delegate_code_task instead."
        ),
        {
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The task for the subagent to perform",
                },
                "type": {
                    "type": "string",
                    "description": "Subagent type: explore (read-only, cheap) or general (full tools)",
                    "default": "explore",
                },
                "context": {
                    "type": "string",
                    "description": "Optional extra context (file paths, decisions, constraints)",
                    "default": "",
                },
            },
            "required": ["task"],
        },
        spawn_subagent,
    )


def _format_subagent_result(subagent_type: str, result: SubagentResult, max_iterations: int) -> str:
    lines = [
        f"subagent_type: {subagent_type}",
        f"status: {result.status}",
        f"iterations: {result.iterations_used}/{max_iterations}",
        f"tokens: {result.input_tokens} in, {result.output_tokens} out",
        "",
        result.text,
    ]
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_subagent_tools.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add archon/subagents/tools.py tests/test_subagent_tools.py
git commit -m "feat: add spawn_subagent tool with handler and result formatting"
```

---

### Task 6: Wire into ToolRegistry and Agent token rollup

**Files:**
- Modify: `archon/tools.py:14` (imports), `archon/tools.py:234-243` (_register_builtins)
- Modify: `archon/subagents/tools.py` (token rollup via parent's _record_llm_usage)
- Test: `tests/test_subagent_integration.py`

- [ ] **Step 1: Write failing integration test**

```python
# tests/test_subagent_integration.py
"""Integration test: spawn_subagent is available in a fresh ToolRegistry."""
from archon.config import Config
from archon.tools import ToolRegistry


def test_spawn_subagent_registered_in_default_registry():
    cfg = Config()
    reg = ToolRegistry(config=cfg, confirmer=lambda l, lv: True)
    assert "spawn_subagent" in reg.tools
    schema = reg.tools["spawn_subagent"]
    props = schema["input_schema"]["properties"]
    assert "task" in props
    assert "type" in props
    assert "context" in props
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_subagent_integration.py -v`
Expected: FAIL — `spawn_subagent` not registered

- [ ] **Step 3: Wire register_subagent_tools into _register_builtins**

In `archon/tools.py`, add import at line 14 area:

```python
from archon.subagents.tools import register_subagent_tools
```

In `_register_builtins()` (around line 243, after `register_worker_tools(self)`), add:

```python
        register_subagent_tools(self)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_subagent_integration.py -v`
Expected: PASS

- [ ] **Step 5: Add token rollup to spawn_subagent handler**

In `archon/subagents/tools.py`, after `result = runner.run()`, before the return, add token rollup logic. The handler needs access to the parent agent for `_record_llm_usage()`. Since tools don't have direct agent access, we use the registry's execute event handler to proxy usage recording.

Add this after `result = runner.run()`:

```python
        # Roll up token usage via registry event handler so /cost ledger is accurate
        _emit_subagent_usage(
            registry,
            subagent_type=type_key,
            model=model,
            provider=provider,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
```

Add the helper function:

```python
def _emit_subagent_usage(registry, *, subagent_type, model, provider, input_tokens, output_tokens):
    """Emit usage event so parent's _record_llm_usage picks it up for /cost."""
    handler = getattr(registry, "_execute_event_handler", None)
    if handler is None:
        return
    try:
        handler("subagent_usage", {
            "source": f"subagent:{subagent_type}",
            "provider": provider,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        })
    except Exception:
        pass
```

- [ ] **Step 6: Run full test suite for regressions**

Run: `pytest tests/ -x -q`
Expected: All existing tests pass

- [ ] **Step 7: Commit**

```bash
git add archon/tools.py archon/subagents/tools.py tests/test_subagent_integration.py
git commit -m "feat: wire spawn_subagent into ToolRegistry and add token usage events"
```

---

### Task 7: Agent-side token rollup for /cost

**Files:**
- Modify: `archon/agent.py:443-458` (_on_tool_execute_event)
- Test: `tests/test_subagent_token_rollup.py`

- [ ] **Step 1: Write failing test for token rollup**

```python
# tests/test_subagent_token_rollup.py
"""Test that subagent usage events update Agent token counters and ledger."""
from unittest.mock import MagicMock, patch

from archon.agent import Agent
from archon.config import Config


def test_subagent_usage_event_updates_counters():
    cfg = Config()
    cfg.llm.api_key = "test-key"
    agent = Agent(cfg)
    agent.total_input_tokens = 1000
    agent.total_output_tokens = 500

    # Simulate the event the tool handler emits
    agent._on_tool_execute_event("subagent_usage", {
        "source": "subagent:explore",
        "provider": "anthropic",
        "model": "claude-haiku-4-5-20251001",
        "input_tokens": 200,
        "output_tokens": 100,
    })

    assert agent.total_input_tokens == 1200
    assert agent.total_output_tokens == 600
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_subagent_token_rollup.py -v`
Expected: FAIL — `_on_tool_execute_event` doesn't handle `subagent_usage` events

- [ ] **Step 3: Handle subagent_usage events in Agent**

In `archon/agent.py` `_on_tool_execute_event()` method (around line 443), add handling before the existing `if kind == "ux_event"` check:

```python
        if kind == "subagent_usage":
            in_tokens = int(hook_payload.get("input_tokens", 0) or 0)
            out_tokens = int(hook_payload.get("output_tokens", 0) or 0)
            self.total_input_tokens += in_tokens
            self.total_output_tokens += out_tokens
            # Record to ledger for /cost workflow totals
            try:
                from archon.llm import LLMResponse
                dummy_response = LLMResponse(
                    text=None, tool_calls=[], raw_content=[],
                    input_tokens=in_tokens, output_tokens=out_tokens,
                )
                self._record_llm_usage(
                    turn_id=self.last_turn_id or "subagent",
                    source=str(hook_payload.get("source", "subagent") or "subagent"),
                    response=dummy_response,
                )
            except Exception:
                pass
            return
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_subagent_token_rollup.py -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All existing tests pass

- [ ] **Step 6: Commit**

```bash
git add archon/agent.py tests/test_subagent_token_rollup.py
git commit -m "feat: handle subagent usage events in Agent for /cost token rollup"
```

---

### Task 8: Update __init__.py exports and final wiring

**Files:**
- Modify: `archon/subagents/__init__.py`
- Test: `tests/test_subagent_exports.py`

- [ ] **Step 1: Write test for public API exports**

```python
# tests/test_subagent_exports.py
"""Test subagent package public API."""


def test_public_imports():
    from archon.subagents import SubagentConfig, SubagentResult
    from archon.subagents import register_subagent_tools
    from archon.subagents import build_subagent_registry
    assert SubagentConfig is not None
    assert SubagentResult is not None
    assert register_subagent_tools is not None
    assert build_subagent_registry is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_subagent_exports.py -v`
Expected: FAIL — imports not exported from `__init__.py`

- [ ] **Step 3: Update __init__.py**

```python
# archon/subagents/__init__.py
"""Native subagent system."""
from archon.subagents.registry import build_subagent_registry
from archon.subagents.runner import SubagentConfig, SubagentResult
from archon.subagents.tools import register_subagent_tools

__all__ = [
    "SubagentConfig",
    "SubagentResult",
    "build_subagent_registry",
    "register_subagent_tools",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_subagent_exports.py -v`
Expected: PASS

- [ ] **Step 5: Run full test suite — all tests including new ones**

Run: `pytest tests/ -x -q`
Expected: ALL PASS (existing + new)

- [ ] **Step 6: Commit**

```bash
git add archon/subagents/__init__.py tests/test_subagent_exports.py
git commit -m "feat: export subagent public API from package __init__"
```

---

### Task 9: Final verification — end-to-end and regression

**Files:**
- No new files

- [ ] **Step 1: Run all subagent tests together**

Run: `pytest tests/test_subagent_*.py -v`
Expected: ALL PASS

- [ ] **Step 2: Run full test suite**

Run: `pytest tests/ -q`
Expected: ALL PASS, no regressions

- [ ] **Step 3: Verify spawn_subagent appears in default tool schemas**

```python
# Quick verification (run in python -c or a temp test)
from archon.config import Config
from archon.tools import ToolRegistry
reg = ToolRegistry(config=Config(), confirmer=lambda l, lv: True)
names = [s["name"] for s in reg.get_schemas()]
assert "spawn_subagent" in names
assert "delegate_code_task" in names  # coexistence
print("OK: both tools registered")
```

- [ ] **Step 4: Verify explore registry does NOT have spawn_subagent**

```python
from archon.config import Config
from archon.subagents.registry import build_subagent_registry
reg = build_subagent_registry(Config(), "explore", confirmer=lambda l, lv: True)
assert "spawn_subagent" not in reg.tools
assert "delegate_code_task" not in reg.tools
assert "shell" in reg.tools
assert "read_file" in reg.tools
print("OK: explore has correct tools")
```

- [ ] **Step 5: Commit final verification (if any test fixes were needed)**

```bash
git add -A
git commit -m "test: final verification of native subagent system"
```
