# Feedback Loop Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Archon's tool execution visible and alive — structured events, enriched summaries, shell streaming, edit diffs — across CLI and Telegram.

**Architecture:** Hybrid approach. A `ToolContext` object is injected into opted-in handlers via `_ctx` kwarg. Handlers write metadata and optionally emit mid-execution UX events. `ToolRegistry.execute()` reads metadata post-return to build enriched `tool_end` summaries. Two thin surface renderers (CLI, Telegram) format structured events for display. A shared `stderr_lock` prevents garbled CLI output.

**Tech Stack:** Python 3.11+, `subprocess.Popen`, `difflib.unified_diff`, `threading.Lock`, `inspect.signature`

**Spec:** `docs/superpowers/specs/2026-03-23-feedback-loop-overhaul-design.md`

---

## File Structure

### New files
- `archon/ux/tool_context.py` — `ToolContext` dataclass + `build_tool_summary()` function
- `archon/ux/renderers.py` — shared rendering logic (summary generation, diff truncation, output collapse)
- `archon/ux/cli_renderer.py` — CLI surface renderer (ANSI formatting, stderr output)
- `archon/ux/telegram_renderer.py` — Telegram surface renderer (Markdown formatting, batching)
- `tests/test_tool_context.py` — ToolContext + summary generation tests
- `tests/test_ux_renderers.py` — shared renderer logic tests
- `tests/test_cli_renderer.py` — CLI renderer tests
- `tests/test_telegram_renderer.py` — Telegram renderer tests
- `tests/test_shell_streaming.py` — shell Popen streaming tests
- `tests/test_edit_diff.py` — edit_file diff generation tests

### Modified files
- `archon/ux/events.py` — add `session_id` field to `UXEvent`, add new event constructors
- `archon/tools.py` — inject `ToolContext`, emit enriched events post-execution
- `archon/tooling/filesystem_tools.py` — handlers accept `_ctx`, write metadata, shell uses Popen, edit_file computes diffs
- `archon/cli_ui.py` — add `stderr_lock`, pass to spinner
- `archon/cli_interactive_commands.py` — wire CLI renderer, replace spinner-only feedback
- `archon/adapters/telegram.py` — wire Telegram renderer, session-scoped event routing
- `archon/ux/terminal_feed.py` — use `stderr_lock`
- `archon/agent.py` — pass `session_id` to ToolRegistry

---

## Task 1: ToolContext dataclass + signature-based injection

**Files:**
- Create: `archon/ux/tool_context.py`
- Modify: `archon/tools.py:95-153`
- Test: `tests/test_tool_context.py`

- [ ] **Step 1: Write failing tests for ToolContext and injection**

```python
# tests/test_tool_context.py
"""Tests for ToolContext creation and _ctx injection into handlers."""

import inspect

from archon.ux.tool_context import ToolContext


def test_tool_context_fields():
    events = []
    ctx = ToolContext(tool_name="shell", session_id="s1", emit=events.append, meta={})
    assert ctx.tool_name == "shell"
    assert ctx.session_id == "s1"
    assert ctx.meta == {}
    ctx.meta["exit_code"] = 0
    assert ctx.meta["exit_code"] == 0


def test_tool_context_emit_calls_callback():
    events = []
    ctx = ToolContext(tool_name="shell", session_id="s1", emit=events.append, meta={})
    ctx.emit("fake_event")
    assert events == ["fake_event"]


def test_handler_accepts_ctx_detected():
    """inspect.signature detects _ctx parameter."""
    def with_ctx(command: str, _ctx: ToolContext | None = None) -> str:
        return "ok"

    def without_ctx(command: str) -> str:
        return "ok"

    sig_with = inspect.signature(with_ctx)
    sig_without = inspect.signature(without_ctx)
    assert "_ctx" in sig_with.parameters
    assert "_ctx" not in sig_without.parameters
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/comma/Documents/archon && python -m pytest tests/test_tool_context.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'archon.ux.tool_context'`

- [ ] **Step 3: Implement ToolContext**

```python
# archon/ux/tool_context.py
"""Lightweight execution context for tool handlers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolContext:
    """Passed as _ctx to opted-in tool handlers.

    Handlers write structured metadata to ``meta`` and optionally call
    ``emit()`` with UXEvent instances for mid-execution feedback.
    """

    tool_name: str
    session_id: str
    emit: Callable[[Any], None]
    meta: dict[str, Any] = field(default_factory=dict)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/comma/Documents/archon && python -m pytest tests/test_tool_context.py -v`
Expected: PASS

- [ ] **Step 5: Write failing test for ToolRegistry `_ctx` injection**

```python
# append to tests/test_tool_context.py

from archon.tools import ToolRegistry


def test_registry_injects_ctx_when_handler_accepts_it():
    """ToolRegistry.execute() passes _ctx to handlers that accept it."""
    reg = ToolRegistry(archon_source_dir=None)
    captured = {}

    def my_tool(name: str, _ctx: ToolContext | None = None) -> str:
        captured["ctx"] = _ctx
        if _ctx:
            _ctx.meta["name"] = name
        return f"hello {name}"

    reg.register("my_tool", "test tool", {
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }, my_tool)

    result = reg.execute("my_tool", {"name": "world"})
    assert result == "hello world"
    assert captured["ctx"] is not None
    assert isinstance(captured["ctx"], ToolContext)
    assert captured["ctx"].tool_name == "my_tool"
    assert captured["ctx"].meta["name"] == "world"


def test_registry_skips_ctx_when_handler_does_not_accept_it():
    """ToolRegistry.execute() does not pass _ctx to plain handlers."""
    reg = ToolRegistry(archon_source_dir=None)

    def plain_tool(name: str) -> str:
        return f"hello {name}"

    reg.register("plain_tool", "test", {
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }, plain_tool)

    result = reg.execute("plain_tool", {"name": "world"})
    assert result == "hello world"
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd /home/comma/Documents/archon && python -m pytest tests/test_tool_context.py::test_registry_injects_ctx_when_handler_accepts_it -v`
Expected: FAIL — `captured["ctx"]` is `None` (registry doesn't inject yet)

- [ ] **Step 7: Implement `_ctx` injection in ToolRegistry.execute()**

Modify `archon/tools.py`. Add import at top:

```python
import inspect
from archon.ux.tool_context import ToolContext
```

Replace the `execute` method body (lines 95-153) — add `_ctx` creation and injection between `pre_execute` and handler call:

```python
    def execute(self, name: str, arguments: dict) -> str | SuspensionRequest:
        self._emit_execute_event(
            "pre_execute",
            {"name": name, "arguments": arguments},
        )
        handler = self.handlers.get(name)
        if not handler:
            result = f"Error: Unknown tool '{name}'"
            self._emit_execute_event(
                "post_execute",
                {
                    "name": name,
                    "arguments": arguments,
                    "status": "unknown_tool",
                    "result_is_error": True,
                },
            )
            return result

        # Build ToolContext for handlers that accept _ctx
        ctx = ToolContext(
            tool_name=name,
            session_id=getattr(self, "_session_id", ""),
            emit=self._emit_ux_event,
            meta={},
        )
        handler_kwargs = dict(arguments)
        try:
            sig = inspect.signature(handler)
            if "_ctx" in sig.parameters:
                handler_kwargs["_ctx"] = ctx
        except (ValueError, TypeError):
            pass

        try:
            result = handler(**handler_kwargs)
            if isinstance(result, SuspensionRequest):
                self._emit_execute_event(
                    "post_execute",
                    {
                        "name": name,
                        "arguments": arguments,
                        "status": "suspended",
                        "result_is_error": False,
                        "result_kind": "suspension",
                        "job_id": result.job_id,
                    },
                )
                return result

            # Check if handler signaled blocked via _ctx.meta
            if ctx.meta.get("blocked"):
                self._emit_execute_event(
                    "post_execute",
                    {
                        "name": name,
                        "arguments": arguments,
                        "status": "blocked",
                        "result_is_error": False,
                        "command_preview": ctx.meta.get("command_preview", ""),
                        "meta": dict(ctx.meta),
                    },
                )
                return result

            self._emit_execute_event(
                "post_execute",
                {
                    "name": name,
                    "arguments": arguments,
                    "status": "ok",
                    "result_is_error": False,
                    "result_length": len(str(result)),
                    "meta": dict(ctx.meta),
                },
            )
            return result
        except Exception as e:
            result = f"Error: {type(e).__name__}: {e}"
            self._emit_execute_event(
                "post_execute",
                {
                    "name": name,
                    "arguments": arguments,
                    "status": "error",
                    "result_is_error": True,
                    "result_length": len(result),
                    "error_type": type(e).__name__,
                    "error": str(e),
                },
            )
            return result
```

Also add a no-op `_emit_ux_event` method and `_session_id` attribute to `ToolRegistry.__init__`:

```python
    def __init__(self, ...):
        # ... existing fields ...
        self._session_id: str = ""

    def set_session_id(self, session_id: str) -> None:
        self._session_id = session_id or ""

    def _emit_ux_event(self, event) -> None:
        """Forward UX events from tool handlers to the execute event handler."""
        if self._execute_event_handler is None:
            return
        try:
            self._execute_event_handler("ux_event", {"event": event})
        except Exception:
            return
```

- [ ] **Step 8: Run all tests to verify injection works and nothing breaks**

Run: `cd /home/comma/Documents/archon && python -m pytest tests/test_tool_context.py tests/test_tools_registry_filesystem.py -v`
Expected: ALL PASS

- [ ] **Step 9: Wire session_id from Agent to ToolRegistry**

Modify `archon/agent.py:153` — after `self.session_id = f"session-{time.time_ns()}"`, add:

```python
        self.tools.set_session_id(self.session_id)
```

- [ ] **Step 10: Run full test suite to confirm no regressions**

Run: `cd /home/comma/Documents/archon && python -m pytest --tb=short -q`
Expected: ALL PASS (744+)

- [ ] **Step 11: Commit**

```bash
git add archon/ux/tool_context.py archon/tools.py archon/agent.py tests/test_tool_context.py
git commit -m "feat: add ToolContext and _ctx injection in ToolRegistry.execute()"
```

---

## Task 2: New UXEvent kinds + session_id + constructors

**Files:**
- Modify: `archon/ux/events.py`
- Test: `tests/test_tool_context.py` (append)

- [ ] **Step 1: Write failing tests for new event kinds**

```python
# append to tests/test_tool_context.py

from archon.ux.events import (
    UXEvent,
    tool_running,
    tool_blocked,
    tool_diff,
    tool_end,
)


def test_tool_running_output_line():
    evt = tool_running(tool="shell", session_id="s1", detail_type="output_line", line="hello")
    assert evt.kind == "tool_running"
    assert evt.data["tool"] == "shell"
    assert evt.data["session_id"] == "s1"
    assert evt.data["detail_type"] == "output_line"
    assert evt.data["line"] == "hello"


def test_tool_running_heartbeat():
    evt = tool_running(tool="shell", session_id="s1", detail_type="heartbeat", elapsed_s=4.2)
    assert evt.data["detail_type"] == "heartbeat"
    assert evt.data["elapsed_s"] == 4.2


def test_tool_blocked_event():
    evt = tool_blocked(tool="shell", session_id="s1", command_preview="rm -rf /", safety_level="DANGEROUS")
    assert evt.kind == "tool_blocked"
    assert evt.data["command_preview"] == "rm -rf /"


def test_tool_diff_event():
    evt = tool_diff(
        tool="edit_file",
        session_id="s1",
        path="foo.py",
        diff_lines=["-old", "+new"],
        lines_changed=1,
    )
    assert evt.kind == "tool_diff"
    assert evt.data["diff_lines"] == ["-old", "+new"]


def test_tool_end_with_summary():
    evt = tool_end(name="shell", result_summary="shell: exit 0 (5 lines)", session_id="s1")
    assert evt.data["session_id"] == "s1"
    assert evt.data["result"] == "shell: exit 0 (5 lines)"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/comma/Documents/archon && python -m pytest tests/test_tool_context.py::test_tool_running_output_line -v`
Expected: FAIL — `ImportError` (constructors don't exist yet)

- [ ] **Step 3: Add new constructors to events.py**

Modify `archon/ux/events.py`. Add new constructor functions after the existing ones:

```python
def tool_running(
    *,
    tool: str,
    session_id: str = "",
    detail_type: str,
    line: str = "",
    elapsed_s: float = 0.0,
) -> UXEvent:
    return UXEvent(
        kind="tool_running",
        data={
            "tool": tool,
            "session_id": session_id,
            "detail_type": detail_type,
            "line": line,
            "elapsed_s": elapsed_s,
        },
    )


def tool_blocked(
    *,
    tool: str,
    session_id: str = "",
    command_preview: str,
    safety_level: str,
) -> UXEvent:
    return UXEvent(
        kind="tool_blocked",
        data={
            "tool": tool,
            "session_id": session_id,
            "command_preview": command_preview,
            "safety_level": safety_level,
        },
    )


def tool_diff(
    *,
    tool: str,
    session_id: str = "",
    path: str,
    diff_lines: list[str],
    lines_changed: int,
    context_before: str = "",
) -> UXEvent:
    return UXEvent(
        kind="tool_diff",
        data={
            "tool": tool,
            "session_id": session_id,
            "path": path,
            "diff_lines": diff_lines,
            "lines_changed": lines_changed,
            "context_before": context_before,
        },
    )
```

**Naming note**: The existing `tool_start`/`tool_end` events use `name` for the tool name. New events (`tool_running`, `tool_blocked`, `tool_diff`) use `tool`. This inconsistency is intentional — changing the existing field name would break downstream consumers. New events use the cleaner name. Renderers should handle both: check `d.get("tool") or d.get("name")` when needed.

Also update existing `tool_start` and `tool_end` constructors to accept optional `session_id`:

```python
def tool_start(name: str, args_summary: str = "", *, session_id: str = "") -> UXEvent:
    return UXEvent(kind="tool_start", data={"name": name, "args": args_summary, "session_id": session_id})


def tool_end(name: str, result_summary: str = "", *, session_id: str = "") -> UXEvent:
    return UXEvent(kind="tool_end", data={"name": name, "result": result_summary, "session_id": session_id})
```

Add `render_text()` branches for the new kinds inside `UXEvent.render_text()`:

```python
        if k == "tool_running":
            tool = d.get("tool", "?")
            if d.get("detail_type") == "output_line":
                return f"│ {d.get('line', '')}"
            return f"[{tool}] running ({d.get('elapsed_s', 0):.0f}s)"
        if k == "tool_blocked":
            tool = d.get("tool", "?")
            preview = d.get("command_preview", "?")
            level = d.get("safety_level", "DANGEROUS")
            return f"⚠ blocked: {preview} ({level}) — /approve or /deny"
        if k == "tool_diff":
            path = d.get("path", "?")
            n = d.get("lines_changed", 0)
            lines = d.get("diff_lines", [])
            header = f"diff: {path} ({n} line{'s' if n != 1 else ''} changed)"
            if lines:
                return header + "\n" + "\n".join(f"  {l}" for l in lines[:10])
            return header
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/comma/Documents/archon && python -m pytest tests/test_tool_context.py -v`
Expected: ALL PASS

- [ ] **Step 5: Run existing event tests to confirm backward compat**

Run: `cd /home/comma/Documents/archon && python -m pytest tests/test_terminal_feed.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add archon/ux/events.py tests/test_tool_context.py
git commit -m "feat: add tool_running, tool_blocked, tool_diff UXEvent constructors with session_id"
```

---

## Task 3: build_tool_summary + shared renderer logic

**Files:**
- Create: `archon/ux/renderers.py`
- Test: `tests/test_ux_renderers.py`

- [ ] **Step 1: Write failing tests for summary generation**

```python
# tests/test_ux_renderers.py
"""Tests for shared rendering logic."""

from archon.ux.renderers import build_tool_summary, collapse_output_lines, truncate_diff_lines


class TestBuildToolSummary:
    def test_shell_from_meta(self):
        meta = {"exit_code": 0, "line_count": 14}
        assert build_tool_summary("shell", meta, "") == "shell: exit 0 (14 lines)"

    def test_shell_error_from_meta(self):
        meta = {"exit_code": 1, "line_count": 3}
        assert build_tool_summary("shell", meta, "") == "shell: exit 1 (3 lines)"

    def test_read_file_from_meta(self):
        meta = {"path": "/etc/pacman.conf", "line_count": 74}
        assert build_tool_summary("read_file", meta, "") == "read: /etc/pacman.conf (74 lines)"

    def test_edit_file_from_meta(self):
        meta = {"path": "agent.py", "line_number": 42, "lines_changed": 1}
        assert build_tool_summary("edit_file", meta, "") == "edit: agent.py:42 (1 line changed)"

    def test_write_file_new_from_meta(self):
        meta = {"path": "config.py", "line_count": 38, "is_new": True}
        assert build_tool_summary("write_file", meta, "") == "write: config.py (new, 38 lines)"

    def test_write_file_existing_from_meta(self):
        meta = {"path": "config.py", "line_count": 38, "is_new": False}
        assert build_tool_summary("write_file", meta, "") == "write: config.py (38 lines)"

    def test_grep_from_meta(self):
        meta = {"pattern": "max_iter", "match_count": 3, "file_count": 2}
        assert build_tool_summary("grep", meta, "") == "grep: 'max_iter' -> 3 matches in 2 files"

    def test_glob_from_meta(self):
        meta = {"pattern": "*.py", "file_count": 47}
        assert build_tool_summary("glob", meta, "") == "glob: *.py -> 47 files"

    def test_fallback_unknown_tool_empty_meta(self):
        assert build_tool_summary("web_search", {}, "some result") == "web_search: done"

    def test_fallback_parse_shell_exit_code(self):
        """Fallback string parsing when meta is empty."""
        result_str = "hello world\n[exit_code=0]"
        assert build_tool_summary("shell", {}, result_str) == "shell: exit 0 (1 line)"


class TestCollapseOutputLines:
    def test_short_output_unchanged(self):
        lines = [f"line {i}" for i in range(5)]
        assert collapse_output_lines(lines, max_lines=20) == lines

    def test_long_output_collapsed(self):
        lines = [f"line {i}" for i in range(30)]
        result = collapse_output_lines(lines, max_lines=20)
        assert len(result) == 14  # 8 head + 1 elision + 5 tail
        assert result[0] == "line 0"
        assert result[7] == "line 7"
        assert "... (16 more lines)" in result[8]
        assert result[-1] == "line 29"


class TestTruncateDiffLines:
    def test_short_diff_unchanged(self):
        lines = ["-old", "+new"]
        assert truncate_diff_lines(lines, max_lines=10) == lines

    def test_long_diff_truncated(self):
        lines = [f"-line {i}" for i in range(15)]
        result = truncate_diff_lines(lines, max_lines=10)
        assert len(result) == 11  # 10 lines + elision
        assert "... (5 more lines changed)" in result[-1]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/comma/Documents/archon && python -m pytest tests/test_ux_renderers.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement renderers.py**

```python
# archon/ux/renderers.py
"""Shared rendering logic for tool execution feedback."""

from __future__ import annotations

import re

_SHELL_EXIT_RE = re.compile(r"\[exit_code=(-?\d+)\]\s*$")


def build_tool_summary(tool_name: str, meta: dict, result_str: str) -> str:
    """Build a one-liner summary from handler metadata, with fallback to string parsing."""
    if tool_name == "shell":
        exit_code = meta.get("exit_code")
        line_count = meta.get("line_count")
        if exit_code is not None and line_count is not None:
            return f"shell: exit {exit_code} ({line_count} lines)"
        # Fallback: parse result string
        m = _SHELL_EXIT_RE.search(result_str)
        if m:
            code = int(m.group(1))
            body = result_str[: m.start()]
            lines = len(body.strip().splitlines()) if body.strip() else 0
            return f"shell: exit {code} ({lines} line{'s' if lines != 1 else ''})"

    if tool_name == "read_file":
        path = meta.get("path")
        line_count = meta.get("line_count")
        if path is not None and line_count is not None:
            return f"read: {path} ({line_count} lines)"

    if tool_name == "edit_file":
        path = meta.get("path")
        line_number = meta.get("line_number")
        lines_changed = meta.get("lines_changed")
        if path is not None and lines_changed is not None:
            loc = f":{line_number}" if line_number else ""
            word = "line changed" if lines_changed == 1 else "lines changed"
            return f"edit: {path}{loc} ({lines_changed} {word})"

    if tool_name == "write_file":
        path = meta.get("path")
        line_count = meta.get("line_count")
        is_new = meta.get("is_new")
        if path is not None and line_count is not None:
            new_tag = "new, " if is_new else ""
            return f"write: {path} ({new_tag}{line_count} lines)"

    if tool_name == "grep":
        pattern = meta.get("pattern")
        match_count = meta.get("match_count")
        file_count = meta.get("file_count")
        if pattern is not None and match_count is not None:
            return f"grep: '{pattern}' -> {match_count} matches in {file_count} files"

    if tool_name == "glob":
        pattern = meta.get("pattern")
        file_count = meta.get("file_count")
        if pattern is not None and file_count is not None:
            return f"glob: {pattern} -> {file_count} files"

    return f"{tool_name}: done"


def collapse_output_lines(
    lines: list[str],
    max_lines: int = 20,
    head: int = 8,
    tail: int = 5,
) -> list[str]:
    """Collapse long output: first N + elision + last M."""
    if len(lines) <= max_lines:
        return lines
    elided = len(lines) - head - tail
    return lines[:head] + [f"... ({elided} more lines)"] + lines[-tail:]


def truncate_diff_lines(lines: list[str], max_lines: int = 10) -> list[str]:
    """Truncate diff display with elision count."""
    if len(lines) <= max_lines:
        return lines
    remaining = len(lines) - max_lines
    return lines[:max_lines] + [f"... ({remaining} more lines changed)"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/comma/Documents/archon && python -m pytest tests/test_ux_renderers.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add archon/ux/renderers.py tests/test_ux_renderers.py
git commit -m "feat: add build_tool_summary and shared rendering helpers"
```

---

## Task 4: stderr_lock + CLI renderer

**Files:**
- Create: `archon/ux/cli_renderer.py`
- Modify: `archon/cli_ui.py:36-68` (spinner uses lock)
- Modify: `archon/ux/terminal_feed.py:38` (uses lock)
- Test: `tests/test_cli_renderer.py`

- [ ] **Step 1: Write failing tests for CLI renderer**

```python
# tests/test_cli_renderer.py
"""Tests for CLI surface renderer."""

from archon.ux.cli_renderer import CLIRenderer
from archon.ux import events


class _Buffer:
    def __init__(self):
        self.parts = []

    def write(self, text: str) -> None:
        self.parts.append(text)

    def flush(self) -> None:
        pass

    def render(self) -> str:
        return "".join(self.parts)


def test_render_tool_end_completed():
    buf = _Buffer()
    r = CLIRenderer(write_fn=buf.write, flush_fn=buf.flush)
    evt = events.tool_end("shell", "shell: exit 0 (5 lines)", session_id="s1")
    r.render_event(evt, status="completed")
    output = buf.render()
    assert "✓" in output
    assert "shell: exit 0 (5 lines)" in output


def test_render_tool_end_failed():
    buf = _Buffer()
    r = CLIRenderer(write_fn=buf.write, flush_fn=buf.flush)
    evt = events.tool_end("shell", "shell: exit 1 (error)", session_id="s1")
    r.render_event(evt, status="failed")
    output = buf.render()
    assert "✗" in output
    assert "shell: exit 1 (error)" in output


def test_render_tool_blocked():
    buf = _Buffer()
    r = CLIRenderer(write_fn=buf.write, flush_fn=buf.flush)
    evt = events.tool_blocked(tool="shell", session_id="s1", command_preview="pacman -Syu", safety_level="DANGEROUS")
    r.render_event(evt)
    output = buf.render()
    assert "⚠" in output
    assert "pacman -Syu" in output
    assert "/approve" in output


def test_render_tool_running_output_line():
    buf = _Buffer()
    r = CLIRenderer(write_fn=buf.write, flush_fn=buf.flush)
    evt = events.tool_running(tool="shell", session_id="s1", detail_type="output_line", line="==> Building...")
    r.render_event(evt)
    output = buf.render()
    assert "│" in output
    assert "==> Building..." in output


def test_render_tool_diff():
    buf = _Buffer()
    r = CLIRenderer(write_fn=buf.write, flush_fn=buf.flush)
    evt = events.tool_diff(
        tool="edit_file", session_id="s1", path="foo.py",
        diff_lines=["-old", "+new"], lines_changed=1,
    )
    r.render_event(evt)
    output = buf.render()
    assert "-old" in output
    assert "+new" in output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/comma/Documents/archon && python -m pytest tests/test_cli_renderer.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement CLIRenderer**

```python
# archon/ux/cli_renderer.py
"""CLI surface renderer — ANSI-formatted tool feedback on stderr."""

from __future__ import annotations

import threading
from collections.abc import Callable

from archon.ux.events import UXEvent
from archon.ux.renderers import truncate_diff_lines

ANSI_RESET = "\033[0m"
ANSI_DIM = "\033[2m"
ANSI_RED = "\033[91m"
ANSI_GREEN = "\033[92m"
ANSI_YELLOW = "\033[93m"


class CLIRenderer:
    """Formats UXEvents for terminal display on stderr."""

    def __init__(
        self,
        *,
        write_fn: Callable[[str], object] | None = None,
        flush_fn: Callable[[], object] | None = None,
        lock: threading.Lock | None = None,
    ) -> None:
        import sys
        self._write = write_fn or sys.stderr.write
        self._flush = flush_fn or sys.stderr.flush
        self._lock = lock or threading.Lock()

    def render_event(self, event: UXEvent, *, status: str = "") -> None:
        k = event.kind
        d = event.data
        if k == "tool_end":
            summary = d.get("result", "") or f"{d.get('name', '?')}: done"
            if status == "failed":
                self._emit(f"{ANSI_RED}✗ {summary}{ANSI_RESET}")
            else:
                self._emit(f"{ANSI_DIM}✓ {summary}{ANSI_RESET}")
        elif k == "tool_blocked":
            preview = d.get("command_preview", "?")
            level = d.get("safety_level", "DANGEROUS")
            self._emit(f"{ANSI_YELLOW}⚠ blocked: {preview} ({level}) — /approve or /deny{ANSI_RESET}")
        elif k == "tool_running":
            if d.get("detail_type") == "output_line":
                line = d.get("line", "")
                self._emit(f"{ANSI_DIM}│ {line}{ANSI_RESET}")
            elif d.get("detail_type") == "heartbeat":
                elapsed = d.get("elapsed_s", 0)
                tool = d.get("tool", "?")
                self._emit(f"{ANSI_DIM}⠹ {tool} ({elapsed:.0f}s){ANSI_RESET}")
        elif k == "tool_diff":
            diff_lines = truncate_diff_lines(d.get("diff_lines", []))
            for line in diff_lines:
                if line.startswith("-"):
                    self._emit(f"  {ANSI_RED}{line}{ANSI_RESET}")
                elif line.startswith("+"):
                    self._emit(f"  {ANSI_GREEN}{line}{ANSI_RESET}")
                else:
                    self._emit(f"  {ANSI_DIM}{line}{ANSI_RESET}")

    def _emit(self, text: str) -> None:
        with self._lock:
            self._write(f"\r\033[K{text}\n")
            self._flush()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/comma/Documents/archon && python -m pytest tests/test_cli_renderer.py -v`
Expected: ALL PASS

- [ ] **Step 5: Add stderr_lock to _Spinner**

Modify `archon/cli_ui.py`. Add `lock` parameter to `_Spinner.__init__` and use it in `_spin`:

In `__init__` (line 41), add parameter:

```python
    def __init__(self, lock: threading.Lock | None = None):
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._label = "thinking"
        self._lock = lock or threading.Lock()
```

In `stop` (line 53), wrap the write:

```python
    def stop(self):
        if self._thread and self._thread.is_alive():
            self._stop.set()
            self._thread.join(timeout=1)
        with self._lock:
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()
```

In `_spin` (line 61), wrap the write:

```python
    def _spin(self):
        i = 0
        while not self._stop.is_set():
            frame = self.FRAMES[i % len(self.FRAMES)]
            with self._lock:
                sys.stderr.write(f"\r{ANSI_SPINNER}{frame} {self._label}...{ANSI_RESET}")
                sys.stderr.flush()
            i += 1
            self._stop.wait(0.08)
```

- [ ] **Step 6: Add lock parameter to TerminalActivityFeed**

Modify `archon/ux/terminal_feed.py`. Add `lock` parameter to `__init__`:

```python
    def __init__(
        self,
        *,
        prompt_fn: ...,
        input_fn: ...,
        write_fn: ...,
        flush_fn: ...,
        lock: threading.Lock | None = None,
    ) -> None:
        # ... existing ...
        self._lock = lock or threading.Lock()
```

Import `threading` at top. Wrap `emit_text` writes with `self._lock`:

```python
    def emit_text(self, text: str) -> None:
        with self._lock:
            self._write_fn("\r\033[K")
            self._write_fn(sanitize_terminal_notice_text(text))
            self._write_fn("\r\n")
            prompt = self.current_prompt
            buffer_text = strip_readline_prompt_markers(self._safe_text(self._input_fn))
            if prompt or buffer_text:
                self._write_fn(f"{prompt}{buffer_text}")
            self._flush_fn()
```

- [ ] **Step 7: Run existing terminal feed tests + new CLI renderer tests**

Run: `cd /home/comma/Documents/archon && python -m pytest tests/test_terminal_feed.py tests/test_cli_renderer.py -v`
Expected: ALL PASS

- [ ] **Step 8: Commit**

```bash
git add archon/ux/cli_renderer.py archon/cli_ui.py archon/ux/terminal_feed.py tests/test_cli_renderer.py
git commit -m "feat: add CLIRenderer and stderr_lock for safe concurrent terminal output"
```

---

## Task 5: Handler metadata opt-in (read_file, write_file, grep, glob, shell blocked, edit_file blocked)

**Files:**
- Modify: `archon/tooling/filesystem_tools.py:51-280`
- Test: `tests/test_tool_context.py` (append)

- [ ] **Step 1: Write failing tests for handler metadata**

```python
# append to tests/test_tool_context.py
import tempfile
import os
from pathlib import Path

from archon.ux.tool_context import ToolContext


def _make_ctx():
    return ToolContext(tool_name="test", session_id="s1", emit=lambda e: None, meta={})


class TestHandlerMetadata:
    def test_read_file_writes_metadata(self, tmp_path):
        f = tmp_path / "hello.txt"
        f.write_text("line1\nline2\nline3\n")
        from archon.tools import ToolRegistry
        reg = ToolRegistry(archon_source_dir=None, confirmer=lambda _c, _l: True)
        result = reg.execute("read_file", {"path": str(f)})
        # The execute method now captures meta internally; we test via enriched post_execute
        assert "line1" in result  # basic sanity

    def test_shell_blocked_writes_meta(self):
        from archon.tools import ToolRegistry
        captured_events = []
        reg = ToolRegistry(
            archon_source_dir=None,
            confirmer=lambda _c, _l: False,  # reject everything
        )
        reg.set_execute_event_handler(lambda kind, payload: captured_events.append((kind, payload)))
        result = reg.execute("shell", {"command": "echo hi"})
        assert "rejected" in result.lower()
        # post_execute should have status=blocked
        post = [e for e in captured_events if e[0] == "post_execute"]
        assert len(post) == 1
        assert post[0][1]["status"] == "blocked"

    def test_edit_file_blocked_writes_meta(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        from archon.tools import ToolRegistry
        reg = ToolRegistry(
            archon_source_dir=None,
            confirmer=lambda _c, _l: False,
        )
        captured_events = []
        reg.set_execute_event_handler(lambda kind, payload: captured_events.append((kind, payload)))
        result = reg.execute("edit_file", {"path": str(f), "old": "hello", "new": "goodbye"})
        assert "rejected" in result.lower()
        post = [e for e in captured_events if e[0] == "post_execute"]
        assert len(post) == 1
        assert post[0][1]["status"] == "blocked"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/comma/Documents/archon && python -m pytest tests/test_tool_context.py::TestHandlerMetadata -v`
Expected: FAIL — `test_shell_blocked_writes_meta` fails (status is "ok" not "blocked")

- [ ] **Step 3: Update shell handler to accept `_ctx` and write metadata**

Modify `archon/tooling/filesystem_tools.py`. In `register_filesystem_tools`, update the `shell` closure (lines 51-68):

```python
    def shell(command: str, timeout: int = 30, _ctx=None) -> str:
        level = classify(command, registry.archon_source_dir)
        if not registry.confirmer(command, level):
            if _ctx is not None:
                _ctx.meta["blocked"] = True
                _ctx.meta["command_preview"] = command[:240]
            return "Command rejected by safety gate."
        try:
            result = subprocess.run(
                ["bash", "-c", command],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = result.stdout + result.stderr
            body = truncate_text(output, 9800) or "(no output)"
            line_count = len(body.strip().splitlines()) if body.strip() else 0
            if _ctx is not None:
                _ctx.meta["exit_code"] = result.returncode
                _ctx.meta["line_count"] = line_count
            if body.endswith("\n"):
                return f"{body}[exit_code={result.returncode}]"
            return f"{body}\n[exit_code={result.returncode}]"
        except subprocess.TimeoutExpired:
            if _ctx is not None:
                _ctx.meta["exit_code"] = -1
                _ctx.meta["line_count"] = 0
            return f"Error: Command timed out after {timeout}s"
```

- [ ] **Step 4: Update read_file to accept `_ctx` and write metadata**

```python
    def read_file(path: str, offset: int = 0, limit: int = 2000, _ctx=None) -> str:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return f"Error: File not found: {p}"
        if not p.is_file():
            return f"Error: Not a file: {p}"
        rejected = _confirm_read_operation(registry, f"Read file: {p}")
        if rejected is not None:
            if _ctx is not None:
                _ctx.meta["blocked"] = True
                _ctx.meta["command_preview"] = f"read_file: {p}"
            return rejected
        try:
            lines = p.read_text().splitlines()
            selected = lines[offset:offset + limit]
            numbered = [f"{i + offset + 1:>6}\t{line}" for i, line in enumerate(selected)]
            result = "\n".join(numbered)
            if _ctx is not None:
                _ctx.meta["path"] = str(p)
                _ctx.meta["line_count"] = len(selected)
            if len(lines) > offset + limit:
                result += f"\n... ({len(lines) - offset - limit} more lines)"
            return result or "(empty file)"
        except Exception as e:
            return f"Error reading file: {e}"
```

- [ ] **Step 5: Update write_file to accept `_ctx` and write metadata**

```python
    def write_file(path: str, content: str, _ctx=None) -> str:
        p = Path(path).expanduser().resolve()
        is_new = not p.exists()
        home = Path.home().resolve()
        source_root = Path(registry.archon_source_dir).resolve() if registry.archon_source_dir else None
        if _should_confirm_write(registry):
            if not _is_relative_to(p, home):
                if not registry.confirmer(f"Write to {p} (outside $HOME)", Level.DANGEROUS):
                    if _ctx is not None:
                        _ctx.meta["blocked"] = True
                        _ctx.meta["command_preview"] = f"write_file: {p}"
                    return "Write rejected by safety gate."
            else:
                if not registry.confirmer(f"Write file: {p}", Level.DANGEROUS):
                    if _ctx is not None:
                        _ctx.meta["blocked"] = True
                        _ctx.meta["command_preview"] = f"write_file: {p}"
                    return "Write rejected by safety gate."
        if source_root and _is_relative_to(p, source_root):
            if not registry.confirmer(f"Write to own source: {p}", Level.DANGEROUS):
                return "Self-modification rejected."
            auto_commit(registry.archon_source_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        if _ctx is not None:
            _ctx.meta["path"] = str(p)
            _ctx.meta["line_count"] = len(content.splitlines())
            _ctx.meta["is_new"] = is_new
        return f"Wrote {len(content)} bytes to {p}"
```

- [ ] **Step 6: Update edit_file to accept `_ctx` and write metadata (blocked only — no diff yet)**

```python
    def edit_file(path: str, old: str, new: str, _ctx=None) -> str:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return f"Error: File not found: {p}"
        home = Path.home().resolve()
        source_root = Path(registry.archon_source_dir).resolve() if registry.archon_source_dir else None
        if _should_confirm_write(registry):
            if not _is_relative_to(p, home):
                if not registry.confirmer(f"Edit {p} (outside $HOME)", Level.DANGEROUS):
                    if _ctx is not None:
                        _ctx.meta["blocked"] = True
                        _ctx.meta["command_preview"] = f"edit_file: {p}"
                    return "Edit rejected by safety gate."
            else:
                if not registry.confirmer(f"Edit file: {p}", Level.DANGEROUS):
                    if _ctx is not None:
                        _ctx.meta["blocked"] = True
                        _ctx.meta["command_preview"] = f"edit_file: {p}"
                    return "Edit rejected by safety gate."
        if source_root and _is_relative_to(p, source_root):
            safety_path = source_root / "safety.py"
            if p == safety_path:
                return "FORBIDDEN: Cannot modify safety.py through the agent."
            if not registry.confirmer(f"Edit own source: {p}", Level.DANGEROUS):
                return "Self-modification rejected."
            auto_commit(registry.archon_source_dir)
        text = p.read_text()
        if old not in text:
            return "Error: old string not found in file"
        count = text.count(old)
        if count > 1:
            return f"Error: old string appears {count} times (must be unique)"
        new_text = text.replace(old, new, 1)
        p.write_text(new_text)
        if _ctx is not None:
            idx = text.index(old)
            line_number = text[:idx].count("\n") + 1
            _ctx.meta["path"] = str(p)
            _ctx.meta["line_number"] = line_number
            _ctx.meta["lines_changed"] = max(old.count("\n"), new.count("\n")) + 1
        return f"Edited {p} (replaced 1 occurrence)"
```

- [ ] **Step 7: Update glob_files and grep_files to accept `_ctx`**

For `glob_files` (line 204), add `_ctx=None` parameter. Before return, write meta:

```python
    def glob_files(pattern: str, root: str = ".", limit: int = 200, _ctx=None) -> str:
        # ... existing logic unchanged ...
        if _ctx is not None:
            _ctx.meta["pattern"] = pattern
            _ctx.meta["file_count"] = len(matches)
        return "\n".join(matches) if matches else "(no matches)"
```

For `grep_files` (line 236), add `_ctx=None` parameter. Before return, write meta:

```python
    def grep_files(pattern: str, root: str = ".", glob: str = "", limit: int = 200, _ctx=None) -> str:
        # ... existing logic unchanged ...
        file_set = set()
        for m in matches:
            parts = m.split(":", 2)
            if parts:
                file_set.add(parts[0])
        if _ctx is not None:
            _ctx.meta["pattern"] = pattern
            _ctx.meta["match_count"] = len(matches)
            _ctx.meta["file_count"] = len(file_set)
        return "\n".join(matches) if matches else "(no matches)"
```

- [ ] **Step 8: Run tests to verify metadata and blocked detection work**

Run: `cd /home/comma/Documents/archon && python -m pytest tests/test_tool_context.py tests/test_tools_registry_filesystem.py tests/test_filesystem_tools_confirm.py -v`
Expected: ALL PASS

- [ ] **Step 9: Run full test suite**

Run: `cd /home/comma/Documents/archon && python -m pytest --tb=short -q`
Expected: ALL PASS

- [ ] **Step 10: Commit**

```bash
git add archon/tooling/filesystem_tools.py tests/test_tool_context.py
git commit -m "feat: handlers accept _ctx and write structured metadata for tool summaries"
```

---

## Task 6: Wire enriched events into CLI chat loop

**Files:**
- Modify: `archon/cli_interactive_commands.py:350-374`
- Modify: `archon/agent.py:423-426`

- [ ] **Step 1: Update Agent._on_tool_execute_event to emit UX events for new statuses**

Modify `archon/agent.py:423`. The method currently just forwards to hook bus. Add UX event emission for post_execute with enriched summaries:

```python
    def _on_tool_execute_event(self, kind: str, payload: dict) -> None:
        hook_payload = dict(payload or {})
        hook_payload.setdefault("turn_id", self.last_turn_id)
        self._emit_hook(f"tool_registry.{kind}", hook_payload)

        # Emit structured UX events for surfaces
        if kind == "ux_event":
            # Mid-execution events from handlers (tool_running, tool_diff)
            event = payload.get("event")
            if event is not None:
                self._emit_hook("ux.tool_event", {"event": event, "turn_id": self.last_turn_id})
            return

        if kind == "post_execute":
            from archon.ux.renderers import build_tool_summary
            from archon.ux import events as ux_events
            name = payload.get("name", "")
            status = payload.get("status", "")
            meta = payload.get("meta", {})
            result_str = ""  # not in payload; summary will use meta when available

            if status == "blocked":
                event = ux_events.tool_blocked(
                    tool=name,
                    session_id=self.session_id,
                    command_preview=meta.get("command_preview", ""),
                    safety_level="DANGEROUS",
                )
                self._emit_hook("ux.tool_event", {"event": event, "turn_id": self.last_turn_id})
            elif status in ("ok", "error"):
                summary = build_tool_summary(name, meta, result_str)
                event = ux_events.tool_end(name, summary, session_id=self.session_id)
                event_status = "failed" if status == "error" else "completed"
                self._emit_hook("ux.tool_event", {
                    "event": event,
                    "status": event_status,
                    "turn_id": self.last_turn_id,
                })
```

- [ ] **Step 2: Wire CLI renderer in chat_cmd**

Modify `archon/cli_interactive_commands.py`. After the existing hook registrations (line 375), add:

```python
    # Add imports at top of cli_interactive_commands.py:
    import threading
    from archon.ux.cli_renderer import CLIRenderer

    # After line 375 (agent.hooks.register("orchestrator.route", on_route)):
    stderr_lock = threading.Lock()
    cli_renderer = CLIRenderer(lock=stderr_lock)
    # Pass lock to spinner too
    spinner = spinner_cls(lock=stderr_lock)

    def on_tool_ux_event(hook_event: HookEvent):
        payload = hook_event.payload or {}
        event = payload.get("event")
        if isinstance(event, UXEvent):
            status = payload.get("status", "")
            cli_renderer.render_event(event, status=status)

    agent.hooks.register("ux.tool_event", on_tool_ux_event)
```

Note: `spinner_cls` is passed in from the outside. The `_Spinner(lock=...)` change from Task 4 makes this work. The `threading` import already exists in `cli_ui.py` but needs adding to `cli_interactive_commands.py`.

- [ ] **Step 3: Run interactive smoke test manually**

Run: `cd /home/comma/Documents/archon && python -m archon chat`
Type: `read /etc/hostname`
Expected: see `✓ read: /etc/hostname (1 lines)` in dim text before the model's response.

- [ ] **Step 4: Run full test suite**

Run: `cd /home/comma/Documents/archon && python -m pytest --tb=short -q`
Expected: ALL PASS (some test string assertions may need updating — fix any that fail due to new event format)

- [ ] **Step 5: Commit**

```bash
git add archon/agent.py archon/cli_interactive_commands.py
git commit -m "feat: wire enriched tool_end summaries and tool_blocked into CLI chat loop"
```

---

## Task 7: Shell streaming via Popen (Slice 2)

**Files:**
- Modify: `archon/tooling/filesystem_tools.py:51-68` (shell handler)
- Test: `tests/test_shell_streaming.py`

- [ ] **Step 1: Write failing tests for shell streaming**

```python
# tests/test_shell_streaming.py
"""Tests for shell tool Popen streaming."""

from archon.tools import ToolRegistry
from archon.ux.tool_context import ToolContext


def test_shell_streams_output_lines():
    """Shell handler emits tool_running events per output line."""
    emitted = []
    reg = ToolRegistry(archon_source_dir=None, confirmer=lambda _c, _l: True)
    # We test by capturing events from the execute event handler
    events = []
    reg.set_execute_event_handler(lambda kind, payload: events.append((kind, payload)))

    result = reg.execute("shell", {"command": "echo line1 && echo line2 && echo line3"})
    assert "[exit_code=0]" in result
    assert "line1" in result
    assert "line2" in result
    assert "line3" in result
    # Check that ux_event events were emitted with output lines
    ux_events = [e for e in events if e[0] == "ux_event"]
    line_events = [
        e for e in ux_events
        if hasattr(e[1].get("event", None), "data")
        and e[1]["event"].data.get("detail_type") == "output_line"
    ]
    assert len(line_events) >= 3


def test_shell_streaming_preserves_exit_code():
    reg = ToolRegistry(archon_source_dir=None, confirmer=lambda _c, _l: True)
    result = reg.execute("shell", {"command": "echo ok && exit 42"})
    assert "[exit_code=42]" in result


def test_shell_streaming_handles_no_output():
    reg = ToolRegistry(archon_source_dir=None, confirmer=lambda _c, _l: True)
    result = reg.execute("shell", {"command": "true"})
    assert "(no output)" in result
    assert "[exit_code=0]" in result


def test_shell_streaming_timeout():
    reg = ToolRegistry(archon_source_dir=None, confirmer=lambda _c, _l: True)
    result = reg.execute("shell", {"command": "sleep 10", "timeout": 1})
    assert "timed out" in result.lower()


def test_shell_streaming_stderr_merged():
    """stderr is interleaved with stdout via stderr=STDOUT."""
    reg = ToolRegistry(archon_source_dir=None, confirmer=lambda _c, _l: True)
    result = reg.execute("shell", {"command": "echo out && echo err >&2 && echo out2"})
    assert "[exit_code=0]" in result
    assert "out" in result
    assert "err" in result
```

- [ ] **Step 2: Run tests to verify current behavior (some will pass, streaming tests will fail)**

Run: `cd /home/comma/Documents/archon && python -m pytest tests/test_shell_streaming.py -v`
Expected: `test_shell_streams_output_lines` FAILS (no ux_event emissions yet)

- [ ] **Step 3: Rewrite shell handler to use Popen with streaming**

Replace the shell handler in `archon/tooling/filesystem_tools.py:51-68`. Add `import time` to the module imports alongside existing `import subprocess`:

```python
    def shell(command: str, timeout: int = 30, _ctx=None) -> str:
        level = classify(command, registry.archon_source_dir)
        if not registry.confirmer(command, level):
            if _ctx is not None:
                _ctx.meta["blocked"] = True
                _ctx.meta["command_preview"] = command[:240]
            return "Command rejected by safety gate."
        try:
            proc = subprocess.Popen(
                ["bash", "-c", command],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            lines: list[str] = []
            try:
                deadline = time.time() + timeout
                for raw_line in proc.stdout:
                    line = raw_line.rstrip("\n")
                    lines.append(line)
                    if _ctx is not None:
                        from archon.ux.events import tool_running
                        _ctx.emit(tool_running(
                            tool="shell",
                            session_id=_ctx.session_id,
                            detail_type="output_line",
                            line=line,
                        ))
                    if time.time() > deadline:
                        proc.kill()
                        proc.wait(timeout=5)
                        if _ctx is not None:
                            _ctx.meta["exit_code"] = -1
                            _ctx.meta["line_count"] = len(lines)
                        body = "\n".join(lines) if lines else ""
                        body = truncate_text(body, 9800) if body else ""
                        return f"{body}\nError: Command timed out after {timeout}s"
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
                if _ctx is not None:
                    _ctx.meta["exit_code"] = -1
                    _ctx.meta["line_count"] = len(lines)
                body = "\n".join(lines) if lines else ""
                body = truncate_text(body, 9800) if body else ""
                return f"{body}\nError: Command timed out after {timeout}s"

            output = "\n".join(lines)
            body = truncate_text(output, 9800) or "(no output)"
            if _ctx is not None:
                _ctx.meta["exit_code"] = proc.returncode
                _ctx.meta["line_count"] = len(lines) if lines else 0
            if body.endswith("\n"):
                return f"{body}[exit_code={proc.returncode}]"
            return f"{body}\n[exit_code={proc.returncode}]"
        except Exception as e:
            if _ctx is not None:
                _ctx.meta["exit_code"] = -1
                _ctx.meta["line_count"] = 0
            return f"Error: {type(e).__name__}: {e}"
```

- [ ] **Step 4: Run streaming tests**

Run: `cd /home/comma/Documents/archon && python -m pytest tests/test_shell_streaming.py -v`
Expected: ALL PASS

- [ ] **Step 5: Run full test suite to catch regressions**

Run: `cd /home/comma/Documents/archon && python -m pytest --tb=short -q`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add archon/tooling/filesystem_tools.py tests/test_shell_streaming.py
git commit -m "feat: shell tool streams output line-by-line via Popen with stderr=STDOUT"
```

---

## Task 8: Edit file diff generation (Slice 3)

**Files:**
- Modify: `archon/tooling/filesystem_tools.py` (edit_file handler)
- Test: `tests/test_edit_diff.py`

- [ ] **Step 1: Write failing tests for diff generation**

```python
# tests/test_edit_diff.py
"""Tests for edit_file diff generation via _ctx."""

import tempfile
from pathlib import Path

from archon.tools import ToolRegistry


def test_edit_emits_diff_event():
    """edit_file emits a tool_diff UXEvent on success."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("def run():\n    max_iter = 15\n    return max_iter\n")
        path = f.name

    events = []
    reg = ToolRegistry(archon_source_dir=None, confirmer=lambda _c, _l: True)
    reg.set_execute_event_handler(lambda kind, payload: events.append((kind, payload)))

    result = reg.execute("edit_file", {
        "path": path,
        "old": "max_iter = 15",
        "new": "max_iter = 30",
    })
    assert "Edited" in result

    ux_events = [e for e in events if e[0] == "ux_event"]
    diff_events = [
        e for e in ux_events
        if hasattr(e[1].get("event", None), "data")
        and e[1]["event"].kind == "tool_diff"
    ]
    assert len(diff_events) == 1
    diff_data = diff_events[0][1]["event"].data
    assert diff_data["path"] == path
    assert any("-" in line and "15" in line for line in diff_data["diff_lines"])
    assert any("+" in line and "30" in line for line in diff_data["diff_lines"])
    Path(path).unlink()


def test_edit_no_diff_on_failure():
    """edit_file does not emit tool_diff if old string not found."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("hello world\n")
        path = f.name

    events = []
    reg = ToolRegistry(archon_source_dir=None, confirmer=lambda _c, _l: True)
    reg.set_execute_event_handler(lambda kind, payload: events.append((kind, payload)))

    result = reg.execute("edit_file", {
        "path": path,
        "old": "does not exist",
        "new": "replacement",
    })
    assert "not found" in result

    ux_events = [e for e in events if e[0] == "ux_event"]
    diff_events = [
        e for e in ux_events
        if hasattr(e[1].get("event", None), "data")
        and e[1]["event"].kind == "tool_diff"
    ]
    assert len(diff_events) == 0
    Path(path).unlink()


def test_edit_skips_diff_for_large_files():
    """edit_file skips diff computation for files >50KB."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        content = "x" * 60_000 + "\nfind_me = 1\n"
        f.write(content)
        path = f.name

    events = []
    reg = ToolRegistry(archon_source_dir=None, confirmer=lambda _c, _l: True)
    reg.set_execute_event_handler(lambda kind, payload: events.append((kind, payload)))

    result = reg.execute("edit_file", {
        "path": path,
        "old": "find_me = 1",
        "new": "find_me = 2",
    })
    assert "Edited" in result

    ux_events = [e for e in events if e[0] == "ux_event"]
    diff_events = [
        e for e in ux_events
        if hasattr(e[1].get("event", None), "data")
        and e[1]["event"].kind == "tool_diff"
    ]
    assert len(diff_events) == 0  # skipped — file too large
    Path(path).unlink()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/comma/Documents/archon && python -m pytest tests/test_edit_diff.py -v`
Expected: `test_edit_emits_diff_event` FAILS (no diff event emitted)

- [ ] **Step 3: Add diff computation to edit_file handler**

Modify `archon/tooling/filesystem_tools.py`. Update the edit_file handler. After the successful `p.write_text(new_text)` and before the return, add diff computation:

```python
        text = p.read_text()
        if old not in text:
            return "Error: old string not found in file"
        count = text.count(old)
        if count > 1:
            return f"Error: old string appears {count} times (must be unique)"
        new_text = text.replace(old, new, 1)
        p.write_text(new_text)

        idx = text.index(old)
        line_number = text[:idx].count("\n") + 1
        lines_changed = max(old.count("\n"), new.count("\n")) + 1

        if _ctx is not None:
            _ctx.meta["path"] = str(p)
            _ctx.meta["line_number"] = line_number
            _ctx.meta["lines_changed"] = lines_changed
            # Emit diff event (skip for files >50KB to avoid performance hit)
            if len(text) <= 50_000:
                import difflib
                old_lines = text.splitlines()
                new_lines = new_text.splitlines()
                diff = list(difflib.unified_diff(
                    old_lines, new_lines, n=1, lineterm="",
                ))
                # Skip the first 2 header lines (--- a, +++ b) and @@ hunk headers
                diff_body = [
                    line for line in diff[2:]
                    if not line.startswith("@@")
                ]
                if diff_body:
                    from archon.ux.events import tool_diff as make_tool_diff
                    _ctx.emit(make_tool_diff(
                        tool="edit_file",
                        session_id=_ctx.session_id,
                        path=str(p),
                        diff_lines=diff_body,
                        lines_changed=lines_changed,
                    ))

        return f"Edited {p} (replaced 1 occurrence)"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/comma/Documents/archon && python -m pytest tests/test_edit_diff.py -v`
Expected: ALL PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /home/comma/Documents/archon && python -m pytest --tb=short -q`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add archon/tooling/filesystem_tools.py tests/test_edit_diff.py
git commit -m "feat: edit_file emits tool_diff UXEvent with unified diff on success"
```

---

## Task 9: Telegram renderer + session-scoped routing (Slice 4)

**Files:**
- Create: `archon/ux/telegram_renderer.py`
- Modify: `archon/adapters/telegram.py`
- Test: `tests/test_telegram_renderer.py`

- [ ] **Step 1: Write failing tests for Telegram renderer**

```python
# tests/test_telegram_renderer.py
"""Tests for Telegram surface renderer."""

from archon.ux.telegram_renderer import TelegramRenderer
from archon.ux import events


def test_render_tool_end_completed():
    r = TelegramRenderer()
    evt = events.tool_end("shell", "shell: exit 0 (5 lines)", session_id="s1")
    text = r.format_event(evt, status="completed")
    assert "✓" in text
    assert "shell: exit 0 (5 lines)" in text


def test_render_tool_end_failed():
    r = TelegramRenderer()
    evt = events.tool_end("shell", "shell: exit 1 (error)", session_id="s1")
    text = r.format_event(evt, status="failed")
    assert "✗" in text


def test_render_tool_blocked():
    r = TelegramRenderer()
    evt = events.tool_blocked(tool="shell", session_id="s1", command_preview="pacman -Syu", safety_level="DANGEROUS")
    text = r.format_event(evt)
    assert "Blocked" in text
    assert "pacman -Syu" in text


def test_render_tool_diff_as_code_block():
    r = TelegramRenderer()
    evt = events.tool_diff(
        tool="edit_file", session_id="s1", path="foo.py",
        diff_lines=["-old", "+new"], lines_changed=1,
    )
    text = r.format_event(evt)
    assert "```diff" in text
    assert "-old" in text
    assert "+new" in text


def test_render_tool_running_output_line():
    r = TelegramRenderer()
    evt = events.tool_running(tool="shell", session_id="s1", detail_type="output_line", line="building...")
    text = r.format_event(evt)
    assert "building..." in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/comma/Documents/archon && python -m pytest tests/test_telegram_renderer.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement TelegramRenderer**

```python
# archon/ux/telegram_renderer.py
"""Telegram surface renderer — Markdown-formatted tool feedback."""

from __future__ import annotations

from archon.ux.events import UXEvent
from archon.ux.renderers import truncate_diff_lines


class TelegramRenderer:
    """Formats UXEvents as Telegram-friendly Markdown strings."""

    def format_event(self, event: UXEvent, *, status: str = "") -> str:
        k = event.kind
        d = event.data

        if k == "tool_end":
            summary = d.get("result", "") or f"{d.get('name', '?')}: done"
            if status == "failed":
                return f"✗ {summary}"
            return f"✓ {summary}"

        if k == "tool_blocked":
            preview = d.get("command_preview", "?")
            level = d.get("safety_level", "DANGEROUS")
            return f"⚠️ **Blocked**: `{preview}` ({level})"

        if k == "tool_running":
            if d.get("detail_type") == "output_line":
                return d.get("line", "")
            if d.get("detail_type") == "heartbeat":
                tool = d.get("tool", "?")
                elapsed = d.get("elapsed_s", 0)
                return f"⏳ {tool} ({elapsed:.0f}s)"

        if k == "tool_diff":
            diff_lines = truncate_diff_lines(d.get("diff_lines", []))
            body = "\n".join(diff_lines)
            return f"```diff\n{body}\n```"

        return event.render_text()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/comma/Documents/archon && python -m pytest tests/test_telegram_renderer.py -v`
Expected: ALL PASS

- [ ] **Step 5: Wire session-scoped routing in Telegram adapter**

Modify `archon/adapters/telegram.py`. This requires changes to the adapter's event handling:

1. Add a `_session_to_chat: dict[str, int]` mapping in `__init__`
2. When creating an Agent for a chat, register: `self._session_to_chat[agent.session_id] = chat_id`
3. Register for `ux.tool_event` hook and route based on `session_id`:

```python
    # In the method that creates/gets agent for a chat:
    self._session_to_chat[agent.session_id] = chat_id

    # Hook registration:
    def _on_tool_ux_event(self, hook_event):
        payload = hook_event.payload or {}
        event = payload.get("event")
        if event is None:
            return
        session_id = event.data.get("session_id", "")
        chat_id = self._session_to_chat.get(session_id)
        if chat_id is None:
            return
        status = payload.get("status", "")
        text = self._telegram_renderer.format_event(event, status=status)
        if text:
            self._send_message(chat_id, text)
```

- [ ] **Step 6: Run Telegram adapter tests**

Run: `cd /home/comma/Documents/archon && python -m pytest tests/test_telegram_adapter.py tests/test_telegram_renderer.py -v`
Expected: ALL PASS

- [ ] **Step 7: Run full test suite**

Run: `cd /home/comma/Documents/archon && python -m pytest --tb=short -q`
Expected: ALL PASS

- [ ] **Step 8: Commit**

```bash
git add archon/ux/telegram_renderer.py archon/adapters/telegram.py tests/test_telegram_renderer.py
git commit -m "feat: add TelegramRenderer with session-scoped event routing"
```

---

## Task 10: Adaptive threshold (heartbeat for slow tools)

**Files:**
- Modify: `archon/tools.py` (add timing logic in execute)
- Modify: `archon/ux/cli_renderer.py` (handle heartbeat suppression for fast tools)
- Test: `tests/test_tool_context.py` (append)

- [ ] **Step 1: Write failing test for heartbeat emission**

```python
# append to tests/test_tool_context.py
import time


def test_slow_non_shell_tool_gets_heartbeat():
    """Non-shell tools taking >2s emit periodic heartbeat events."""
    events = []
    reg = ToolRegistry(archon_source_dir=None, confirmer=lambda _c, _l: True)
    reg.set_execute_event_handler(lambda kind, payload: events.append((kind, payload)))

    # Register a slow custom tool (not shell — shell streams per-line instead)
    import time as time_mod
    def slow_tool(seconds: int = 3, _ctx=None) -> str:
        time_mod.sleep(seconds)
        return "done"

    reg.register("slow_tool", "test slow tool", {
        "properties": {"seconds": {"type": "integer"}},
        "required": [],
    }, slow_tool)

    result = reg.execute("slow_tool", {"seconds": 3})
    assert result == "done"
    ux_events = [e for e in events if e[0] == "ux_event"]
    heartbeats = [
        e for e in ux_events
        if hasattr(e[1].get("event", None), "data")
        and e[1]["event"].data.get("detail_type") == "heartbeat"
    ]
    # Should have at least 1 heartbeat after 2s
    assert len(heartbeats) >= 1


def test_shell_does_not_get_heartbeat():
    """Shell is exempt from heartbeats — it streams per-line instead."""
    events = []
    reg = ToolRegistry(archon_source_dir=None, confirmer=lambda _c, _l: True)
    reg.set_execute_event_handler(lambda kind, payload: events.append((kind, payload)))

    result = reg.execute("shell", {"command": "sleep 3", "timeout": 5})
    ux_events = [e for e in events if e[0] == "ux_event"]
    heartbeats = [
        e for e in ux_events
        if hasattr(e[1].get("event", None), "data")
        and e[1]["event"].data.get("detail_type") == "heartbeat"
    ]
    assert len(heartbeats) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/comma/Documents/archon && python -m pytest tests/test_tool_context.py::test_slow_non_shell_tool_gets_heartbeat -v`
Expected: FAIL (no heartbeat emitted)

- [ ] **Step 3: Add heartbeat timer to ToolRegistry.execute()**

Modify `archon/tools.py`. Add a background timer that fires a heartbeat event if the handler takes >2s. Add this inside `execute()`, after creating the `ToolContext`:

```python
        # Heartbeat timer for adaptive threshold — fires every 2s until handler returns.
        # Shell tools are exempt (they stream per-line instead).
        import time as _time
        _start_time = _time.monotonic()
        _heartbeat_stop = threading.Event()
        _is_shell = (name == "shell")

        def _heartbeat_loop():
            """Emit periodic heartbeats for slow non-shell tools."""
            if _is_shell:
                return  # shell streams per-line; heartbeat is redundant
            while not _heartbeat_stop.wait(2.0):
                from archon.ux.events import tool_running
                elapsed = _time.monotonic() - _start_time
                ctx.emit(tool_running(
                    tool=name,
                    session_id=ctx.session_id,
                    detail_type="heartbeat",
                    elapsed_s=round(elapsed, 1),
                ))

        _hb_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
        _hb_thread.start()

        try:
            result = handler(**handler_kwargs)
            # ... existing post-execution logic ...
        finally:
            _heartbeat_stop.set()
```

Import `threading` at top of `archon/tools.py` (if not already imported).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/comma/Documents/archon && python -m pytest tests/test_tool_context.py::test_slow_non_shell_tool_gets_heartbeat tests/test_tool_context.py::test_shell_does_not_get_heartbeat -v --timeout=10`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /home/comma/Documents/archon && python -m pytest --tb=short -q`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add archon/tools.py tests/test_tool_context.py
git commit -m "feat: emit heartbeat for tools exceeding 2s adaptive threshold"
```

---

## Task 11: Final integration test + test fixups

**Files:**
- Modify: any tests that assert exact UX trace strings

- [ ] **Step 1: Run full test suite and identify failures**

Run: `cd /home/comma/Documents/archon && python -m pytest --tb=long -q 2>&1 | head -100`
Expected: Note any failures related to changed event format or post_execute payload shape.

- [ ] **Step 2: Fix failing tests**

Update test assertions that check exact `post_execute` payload keys (the payload now includes `meta` dict). Update any tests that assert `status: "ok"` for blocked tool calls (now `status: "blocked"`).

- [ ] **Step 3: Run full test suite to confirm all green**

Run: `cd /home/comma/Documents/archon && python -m pytest --tb=short -q`
Expected: ALL PASS

- [ ] **Step 4: Manual smoke test — CLI**

Run: `cd /home/comma/Documents/archon && python -m archon chat`
Test each journey:
1. Type: `what files are in /etc/pacman.d` → expect `✓ read: ...` or `✓ shell: exit 0 ...`
2. Type: `edit archon/agent.py and change max_iter to 30` → expect diff display
3. Type: `run pacman -Syu` → expect `⚠ blocked: pacman -Syu (DANGEROUS)`

- [ ] **Step 5: Commit any test fixups**

```bash
git add -u
git commit -m "test: update assertions for enriched tool execution events"
```

---

## Task 12: Telegram output batching (3s window)

**Files:**
- Modify: `archon/ux/telegram_renderer.py`
- Modify: `archon/adapters/telegram.py`
- Test: `tests/test_telegram_renderer.py` (append)

- [ ] **Step 1: Write failing tests for batching**

```python
# append to tests/test_telegram_renderer.py
import time


def test_batch_collector_accumulates_lines():
    """OutputBatchCollector collects lines and flushes on interval."""
    from archon.ux.telegram_renderer import OutputBatchCollector

    sent = []
    collector = OutputBatchCollector(flush_fn=lambda text: sent.append(text), interval_s=0.1)
    collector.add_line("line1")
    collector.add_line("line2")
    assert len(sent) == 0  # not flushed yet
    collector.flush()
    assert len(sent) == 1
    assert "line1" in sent[0]
    assert "line2" in sent[0]


def test_batch_collector_wraps_in_code_block():
    from archon.ux.telegram_renderer import OutputBatchCollector

    sent = []
    collector = OutputBatchCollector(flush_fn=lambda text: sent.append(text), interval_s=0.1)
    collector.add_line("==> Building...")
    collector.add_line("==> Done")
    collector.flush()
    assert sent[0].startswith("```")
    assert sent[0].endswith("```")


def test_batch_collector_collapses_long_output():
    from archon.ux.telegram_renderer import OutputBatchCollector

    sent = []
    collector = OutputBatchCollector(flush_fn=lambda text: sent.append(text), interval_s=0.1)
    for i in range(30):
        collector.add_line(f"line {i}")
    collector.flush()
    assert "... (" in sent[0]  # elision present
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/comma/Documents/archon && python -m pytest tests/test_telegram_renderer.py::test_batch_collector_accumulates_lines -v`
Expected: FAIL — `ImportError` (OutputBatchCollector doesn't exist)

- [ ] **Step 3: Implement OutputBatchCollector**

Add to `archon/ux/telegram_renderer.py`:

```python
import threading
from archon.ux.renderers import collapse_output_lines


class OutputBatchCollector:
    """Collects shell output lines and flushes as a single code block message.

    Used by the Telegram adapter to avoid sending one message per output line.
    Call add_line() for each tool_running(output_line) event.
    Call flush() when the tool completes or on a timer.
    """

    def __init__(
        self,
        *,
        flush_fn: Callable[[str], None],
        interval_s: float = 3.0,
    ) -> None:
        self._flush_fn = flush_fn
        self._interval_s = interval_s
        self._lines: list[str] = []
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None

    def add_line(self, line: str) -> None:
        with self._lock:
            self._lines.append(line)
            if self._timer is None:
                self._timer = threading.Timer(self._interval_s, self._timed_flush)
                self._timer.daemon = True
                self._timer.start()

    def flush(self) -> None:
        """Flush accumulated lines immediately."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            lines = self._lines[:]
            self._lines.clear()
        if not lines:
            return
        collapsed = collapse_output_lines(lines)
        body = "\n".join(collapsed)
        self._flush_fn(f"```\n{body}\n```")

    def _timed_flush(self) -> None:
        with self._lock:
            self._timer = None
            lines = self._lines[:]
            self._lines.clear()
        if not lines:
            return
        collapsed = collapse_output_lines(lines)
        body = "\n".join(collapsed)
        self._flush_fn(f"```\n{body}\n```")
```

Also add `from collections.abc import Callable` to the imports.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/comma/Documents/archon && python -m pytest tests/test_telegram_renderer.py -v`
Expected: ALL PASS

- [ ] **Step 5: Wire batching in Telegram adapter**

Modify `archon/adapters/telegram.py`. In the `_on_tool_ux_event` handler:

```python
    def _on_tool_ux_event(self, hook_event):
        payload = hook_event.payload or {}
        event = payload.get("event")
        if event is None:
            return
        session_id = event.data.get("session_id", "")
        chat_id = self._session_to_chat.get(session_id)
        if chat_id is None:
            return

        # Batch shell output lines
        if event.kind == "tool_running" and event.data.get("detail_type") == "output_line":
            collector = self._get_or_create_batch_collector(chat_id)
            collector.add_line(event.data.get("line", ""))
            return

        # Flush any pending batch on tool_end
        if event.kind == "tool_end":
            collector = self._batch_collectors.pop(chat_id, None)
            if collector is not None:
                collector.flush()

        status = payload.get("status", "")
        text = self._telegram_renderer.format_event(event, status=status)
        if text:
            self._send_message(chat_id, text)
```

Add `_batch_collectors: dict[int, OutputBatchCollector]` to `__init__` and a `_get_or_create_batch_collector` helper.

- [ ] **Step 6: Run full test suite**

Run: `cd /home/comma/Documents/archon && python -m pytest --tb=short -q`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add archon/ux/telegram_renderer.py archon/adapters/telegram.py tests/test_telegram_renderer.py
git commit -m "feat: add Telegram output batching with 3s flush window"
```
