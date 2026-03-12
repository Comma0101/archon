# Archon UX Overhaul — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring Archon's terminal UX to Claude Code quality. Claude Code is the benchmark — every UX decision here is grounded in what Claude Code actually does and why it works.

**Architecture:** Four layers, ordered by UX impact. Layer 1 (Tool Call Rendering) transforms the biggest visual gap. Layer 2 (Markdown Output + Response Formatting) makes archon's text output readable. Layer 3 (Permission Flow) makes approvals instant. Layer 4 (Session Continuity) wires the v2 backend modules into the agent loop.

**Tech Stack:** Python 3.11+, pytest. One new dependency: `rich` (MIT license, already ubiquitous in Python CLI tools) for markdown rendering and box drawing. Everything else uses existing modules.

**Key Principle:** Claude Code feels polished because of *information density per line* — every line tells you something useful, nothing is wasted, and the visual hierarchy is clear. Archon currently dumps flat cyan text to stderr. This plan fixes that.

---

## The Gap: Archon vs Claude Code (Side-by-Side)

### Tool Call Display

**Claude Code:**
```
╭─ Read archon/agent.py
│  Lines 733-807
╰─ (200 lines)

╭─ Bash
│  pytest tests/ -x -q
│
│  744 passed in 5.96s
╰─ (exit 0)

╭─ Edit archon/config.py
│  Added SKILLS_DIR = DATA_DIR / "skills"
╰─ (1 change)
```

- Box-drawn borders with rounded corners
- Tool name as header (Read, Bash, Edit, Write, Glob, Grep)
- Key args shown inline (file path, command)
- Result summary shown inside the box (truncated, expandable)
- Exit code for commands
- Change count for edits

**Archon currently:**
```
> shell pytest tests/ -x -q
  744 passed in 5.96s
  ... (3 more lines)
```

- Flat text, no structure
- Tool name not human-friendly ("shell" not "Bash")
- Result truncated to 200 chars + line count
- No visual boundary between tool calls

### Response Formatting

**Claude Code:**
- Full markdown rendering: headers, bold, code blocks with syntax highlighting, lists, tables
- Response text streams token-by-token
- Clean visual separation between tool calls and text

**Archon currently:**
```
archon> Here is the result.
        Second line indented 8 spaces.
```
- Raw text, no markdown rendering
- 8-space indent for continuation lines
- No syntax highlighting

### Permission Prompt

**Claude Code:**
```
╭─ Bash: rm -rf /tmp/old-builds
│  Delete old build artifacts
╰─ Allow? (y = yes, n = no, a = always allow Bash)
```
- Same box style as tool calls — visual consistency
- Single keypress (y/n/a), no Enter required
- "always allow" per tool type — remembered for session
- Description of what the command does

**Archon currently:**
```
approval required: dangerous action blocked
request: rm -rf /tmp/old-builds
use /approve, /deny, /approve_next, or /approvals
```
- Requires typing `/approve` + Enter (6 keystrokes vs 1)
- No "always allow per tool" memory
- Breaks conversation flow — user must leave their thought to type a command

### Status/Progress

**Claude Code:**
- Spinner with context: `⠋ Reading archon/agent.py`
- Elapsed time not shown (relies on streaming to feel responsive)
- Tool calls appear inline as they happen — you SEE progress

**Archon currently:**
- Spinner: `⠋ reading file...` (generic label, no file path)
- Tool traces on stderr (may not be visible in all terminals)
- Turn stats after turn: `2.5s | 450 in | 780 out`

### Turn Stats

**Claude Code:**
- Cost shown per-turn: `Cost: $0.03 | Duration: 2.5s`
- Cumulative session cost visible via command

**Archon currently:**
- Token counts: `450 in | 780 out | session: 1,230 tokens`
- No cost estimation
- Route/phase info (useful for debugging, not for UX)

---

## File Structure

### New Files
| File | Purpose |
|------|---------|
| `archon/ux/tool_display.py` | Box-drawn tool call rendering (╭─ Read ... ╰─) |
| `archon/ux/markdown.py` | Markdown-to-ANSI renderer (headers, code blocks, bold, lists) |
| `archon/ux/approval.py` | Single-keypress approval prompt with tool memory |
| `archon/session_lifecycle.py` | Session end distillation and greeting |
| `archon/plan_mode.py` | Plan mode state machine |
| `tests/test_tool_display.py` | Tool display rendering tests |
| `tests/test_markdown_render.py` | Markdown rendering tests |
| `tests/test_approval_ux.py` | Approval flow tests |
| `tests/test_session_lifecycle.py` | Session lifecycle tests |
| `tests/test_plan_mode.py` | Plan mode tests |

### Modified Files
| File | Changes |
|------|---------|
| `archon/agent.py` | Replace `_print_tool_call`/`_print_tool_result` with tool_display; wire compressor; add session-end hook |
| `archon/cli_interactive_commands.py` | Replace spinner with tool display; wire approval UX; add session lifecycle; plan mode toggle |
| `archon/cli_ui.py` | Add markdown response formatting; replace `_format_chat_response` |
| `archon/cli_repl_commands.py` | Add `/plan`, `/go`, `/undo` commands; wire new approval flow |
| `archon/execution/turn_executor.py` | Emit structured tool events; track file modifications |
| `archon/config.py` | Add cost-per-token config for cost display |
| `archon/memory.py` | Add `compact_history_llm()` |

---

## Chunk 1: Box-Drawn Tool Call Display

**Goal:** Replace flat `> tool_name: args` lines with Claude Code-style box-drawn tool displays. This is the single biggest visual improvement.

**Target rendering:**

```
╭─ Read archon/agent.py (lines 733-807)
│
│  def _print_tool_call(name, args, ...):
│      """Print tool call info to stderr..."""
│      ...
│
╰─ 75 lines

╭─ Bash
│  pytest tests/ -x -q
│
│  744 passed in 5.96s
│
╰─ exit 0, 6.2s

╭─ Edit archon/config.py
│  SKILLS_DIR = DATA_DIR / "skills"  (added)
╰─ 1 change

╭─ Write archon/skills/__init__.py
╰─ 38 bytes
```

### Task 1: Tool Display Renderer

**Files:**
- Create: `archon/ux/tool_display.py`
- Test: `tests/test_tool_display.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tool_display.py
"""Box-drawn tool call display tests."""

from archon.ux.tool_display import format_tool_call, format_tool_result, ToolDisplayConfig


def test_format_read_file():
    result = format_tool_call("read_file", {"path": "/home/user/file.py", "offset": 0, "limit": 50})
    assert "Read" in result          # human-friendly name
    assert "file.py" in result       # path shown
    assert "╭" in result             # box drawn


def test_format_shell():
    result = format_tool_call("shell", {"command": "pytest tests/ -x -q"})
    assert "Bash" in result
    assert "pytest" in result
    assert "╭" in result


def test_format_write_file():
    result = format_tool_call("write_file", {"path": "/tmp/test.py", "content": "x" * 100})
    assert "Write" in result
    assert "test.py" in result


def test_format_edit_file():
    result = format_tool_call("edit_file", {"path": "/tmp/test.py"})
    assert "Edit" in result
    assert "test.py" in result


def test_format_tool_result_short():
    result = format_tool_result("shell", "All tests passed.", exit_code=0, elapsed=2.1)
    assert "passed" in result
    assert "exit 0" in result or "0" in result
    assert "╰" in result


def test_format_tool_result_long():
    long_output = "\n".join(f"line {i}" for i in range(50))
    result = format_tool_result("read_file", long_output, max_lines=10)
    assert "╰" in result
    assert "50" in result or "lines" in result.lower()


def test_format_tool_result_empty():
    result = format_tool_result("shell", "", exit_code=0)
    assert "╰" in result


def test_format_generic_tool():
    result = format_tool_call("web_search", {"query": "python asyncio"})
    assert "╭" in result
    assert "web_search" in result or "Search" in result


def test_human_tool_names():
    from archon.ux.tool_display import _human_tool_name
    assert _human_tool_name("shell") == "Bash"
    assert _human_tool_name("read_file") == "Read"
    assert _human_tool_name("write_file") == "Write"
    assert _human_tool_name("edit_file") == "Edit"
    assert _human_tool_name("list_dir") == "List"
    assert _human_tool_name("web_search") == "Search"
    assert _human_tool_name("memory_read") == "Memory Read"


def test_config_no_color():
    cfg = ToolDisplayConfig(use_color=False)
    result = format_tool_call("shell", {"command": "ls"}, config=cfg)
    assert "\033[" not in result  # no ANSI codes
    assert "╭" in result  # box still drawn


def test_result_truncation_preserves_tail():
    """Like Claude Code: show head + tail of long output."""
    lines = [f"line {i}" for i in range(100)]
    result = format_tool_result("shell", "\n".join(lines), max_lines=12)
    assert "line 0" in result       # head preserved
    assert "line 99" in result      # tail preserved
    assert "omitted" in result.lower() or "..." in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tool_display.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement tool display**

```python
# archon/ux/tool_display.py
"""Box-drawn tool call display — Claude Code-style rendering."""

from __future__ import annotations

from dataclasses import dataclass

ANSI_BOX = "\033[2m"          # dim for box chars
ANSI_TOOL_HEADER = "\033[1m"  # bold for tool name
ANSI_TOOL_ARG = "\033[96m"    # cyan for key args (paths, commands)
ANSI_RESULT = "\033[37m"      # light gray for result text
ANSI_META = "\033[90m"        # dim for metadata (line counts, exit codes)
ANSI_RESET = "\033[0m"

_HUMAN_NAMES: dict[str, str] = {
    "shell": "Bash",
    "read_file": "Read",
    "write_file": "Write",
    "edit_file": "Edit",
    "list_dir": "List",
    "web_search": "Search",
    "web_read": "Fetch",
    "memory_read": "Memory Read",
    "memory_write": "Memory Write",
    "memory_lookup": "Memory Search",
    "memory_inbox_add": "Memory Inbox",
    "delegate_code_task": "Delegate",
    "worker_start": "Worker Start",
    "worker_send": "Worker Send",
    "worker_status": "Worker Status",
    "deep_research": "Research",
    "ask_human": "Ask Human",
    "learn_project": "Learn Project",
}


@dataclass
class ToolDisplayConfig:
    use_color: bool = True
    max_result_lines: int = 15
    max_arg_length: int = 80


def _human_tool_name(tool_name: str) -> str:
    return _HUMAN_NAMES.get(tool_name, tool_name.replace("_", " ").title())


def _c(text: str, ansi: str, config: ToolDisplayConfig) -> str:
    if not config.use_color:
        return text
    return f"{ansi}{text}{ANSI_RESET}"


def format_tool_call(name: str, args: dict, *, config: ToolDisplayConfig | None = None) -> str:
    """Render a tool call as a box-drawn header block."""
    cfg = config or ToolDisplayConfig()
    human = _human_tool_name(name)
    header = _build_header(name, human, args, cfg)

    box_top = _c("╭─ ", ANSI_BOX, cfg)
    return f"{box_top}{header}"


def format_tool_result(
    name: str,
    result: str,
    *,
    exit_code: int | None = None,
    elapsed: float | None = None,
    max_lines: int | None = None,
    config: ToolDisplayConfig | None = None,
) -> str:
    """Render a tool result as box body + bottom border."""
    cfg = config or ToolDisplayConfig()
    limit = max_lines or cfg.max_result_lines
    lines = (result or "").splitlines()
    total = len(lines)
    body_lines: list[str] = []

    if total == 0:
        pass
    elif total <= limit:
        for line in lines:
            body_lines.append(f"{_c('│', ANSI_BOX, cfg)}  {line}")
    else:
        # Head + tail strategy (like Claude Code)
        head_count = limit * 2 // 3
        tail_count = limit - head_count - 1
        for line in lines[:head_count]:
            body_lines.append(f"{_c('│', ANSI_BOX, cfg)}  {line}")
        omitted = total - head_count - tail_count
        body_lines.append(
            f"{_c('│', ANSI_BOX, cfg)}  {_c(f'... ({omitted} lines omitted) ...', ANSI_META, cfg)}"
        )
        for line in lines[-tail_count:] if tail_count > 0 else []:
            body_lines.append(f"{_c('│', ANSI_BOX, cfg)}  {line}")

    if body_lines:
        body_lines.insert(0, _c("│", ANSI_BOX, cfg))  # blank separator

    # Footer
    footer_parts: list[str] = []
    if total > 0:
        footer_parts.append(f"{total} lines" if total > 1 else "1 line")
    if exit_code is not None:
        footer_parts.append(f"exit {exit_code}")
    if elapsed is not None:
        footer_parts.append(f"{elapsed:.1f}s")
    footer_text = ", ".join(footer_parts) if footer_parts else ""
    footer = f"{_c('╰─', ANSI_BOX, cfg)} {_c(footer_text, ANSI_META, cfg)}" if footer_text else _c("╰─", ANSI_BOX, cfg)

    parts = body_lines + [footer]
    return "\n".join(parts)


def _build_header(name: str, human: str, args: dict, cfg: ToolDisplayConfig) -> str:
    """Build the header line with tool name and key argument."""
    header = _c(human, ANSI_TOOL_HEADER, cfg)

    if name == "shell":
        cmd = str(args.get("command", "")).strip()
        if cmd:
            return f"{header}\n{_c('│', ANSI_BOX, cfg)}  {_c(cmd[:cfg.max_arg_length], ANSI_TOOL_ARG, cfg)}"
        return header

    if name in ("read_file", "write_file", "edit_file", "list_dir"):
        path = str(args.get("path", "")).strip()
        if path:
            # Shorten to last 3 components
            parts = path.rsplit("/", 3)
            short = "/".join(parts[-3:]) if len(parts) > 3 else path
            suffix = ""
            if name == "read_file":
                offset = args.get("offset", 0)
                limit = args.get("limit")
                if offset or limit:
                    suffix = f" (lines {offset}-{(offset or 0) + (limit or 2000)})"
            elif name == "write_file":
                content = args.get("content", "")
                suffix = f" ({len(content)} bytes)"
            return f"{header} {_c(short, ANSI_TOOL_ARG, cfg)}{suffix}"
        return header

    if name == "web_search":
        query = str(args.get("query", "")).strip()
        if query:
            return f"{header}: {_c(query[:50], ANSI_TOOL_ARG, cfg)}"
        return header

    if name == "delegate_code_task":
        task = str(args.get("task", "")).strip()
        if task:
            return f"{header}: {task[:50]}"
        return header

    # Generic: show first string arg
    for v in args.values():
        if isinstance(v, str) and v.strip():
            return f"{header}: {str(v).strip()[:50]}"
    return header


def format_tool_block(
    name: str,
    args: dict,
    result: str,
    *,
    exit_code: int | None = None,
    elapsed: float | None = None,
    config: ToolDisplayConfig | None = None,
) -> str:
    """Render a complete tool call + result as a single block."""
    call = format_tool_call(name, args, config=config)
    result_block = format_tool_result(
        name, result, exit_code=exit_code, elapsed=elapsed, config=config,
    )
    return f"{call}\n{result_block}"
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_tool_display.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add archon/ux/tool_display.py tests/test_tool_display.py
git commit -m "feat: add box-drawn tool call display renderer"
```

### Task 2: Wire Tool Display into Agent

**Files:**
- Modify: `archon/agent.py` (replace `_print_tool_call` / `_print_tool_result`)
- Modify: `archon/execution/turn_executor.py` (capture exit codes, elapsed time per tool)
- Modify: `archon/cli_interactive_commands.py` (consume new display format)

- [ ] **Step 1: Refactor _print_tool_call**

Replace `_print_tool_call()` at `archon/agent.py:733` with:

```python
def _print_tool_call(name: str, args: dict, prefix: str = "", activity_feed=None):
    from archon.ux.tool_display import format_tool_call
    rendered = format_tool_call(name, args)
    _emit_tool_trace_line(rendered, activity_feed=activity_feed)
```

Replace `_print_tool_result()` at `archon/agent.py:791` with:

```python
def _print_tool_result(
    result: str, prefix: str = "", activity_feed=None,
    *, tool_name: str = "", exit_code: int | None = None, elapsed: float | None = None,
):
    from archon.ux.tool_display import format_tool_result
    rendered = format_tool_result(tool_name, result, exit_code=exit_code, elapsed=elapsed)
    _emit_tool_trace_line(rendered, activity_feed=activity_feed)
```

Note: `_emit_tool_trace_line` currently applies ANSI color globally. The new renderer handles its own coloring, so the wrapper should pass the text through without adding color:

```python
def _emit_tool_trace_line(text: str, *, activity_feed=None, ansi: str = ""):
    """Emit a tool trace line to stderr or activity feed."""
    if activity_feed:
        activity_feed.emit_text(text)
    else:
        # ansi param kept for backward compat but new renderer handles its own color
        if ansi:
            sys.stderr.write(f"{ansi}{text}{ANSI_RESET}\n")
        else:
            sys.stderr.write(f"{text}\n")
        sys.stderr.flush()
```

- [ ] **Step 2: Capture per-tool timing and exit codes**

In `archon/execution/turn_executor.py`, in the tool execution loop:

```python
import time

# Before tool execution:
tool_start_time = time.monotonic()

# Execute tool
result = agent.tools.execute(tool_name, tool_args)

# After execution:
tool_elapsed = time.monotonic() - tool_start_time
tool_exit_code = None
if tool_name == "shell" and isinstance(result, str):
    # Parse exit code from shell result if available
    # The shell tool returns exit code in result metadata
    pass

# Pass to _print_tool_result:
_print_tool_result(
    result_text, activity_feed=activity_feed,
    tool_name=tool_name, exit_code=tool_exit_code, elapsed=tool_elapsed,
)
```

- [ ] **Step 3: Handle multiline output in _emit_tool_trace_line**

The new renderer produces multiline strings (box with body). `_emit_tool_trace_line` and `TerminalActivityFeed.emit_text` need to handle this — either emit the full block as one write, or emit line-by-line.

The simplest approach: `_emit_tool_trace_line` writes the full string (including newlines) to stderr in one call, so the box renders atomically.

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: all pass. Existing tool trace tests may need updates since output format changed.

- [ ] **Step 5: Commit**

```bash
git add archon/agent.py archon/execution/turn_executor.py archon/cli_interactive_commands.py
git commit -m "feat: wire box-drawn tool display into agent and turn executor"
```

---

## Chunk 2: Markdown Response Rendering

**Goal:** Render archon's text responses with basic markdown formatting — headers, bold, code blocks, lists — using ANSI codes. Claude Code renders full markdown; archon currently dumps raw text.

**Target rendering:**
```
archon> ## Changes Made

        I updated **two files**:

        1. `archon/config.py` — added `SKILLS_DIR`
        2. `archon/control/skills.py` — added trigger matching

        ```python
        SKILLS_DIR = DATA_DIR / "skills"
        ```

        All 744 tests pass.
```

vs current:
```
archon> ## Changes Made

        I updated **two files**:
        ...
```

### Task 3: ANSI Markdown Renderer

**Files:**
- Create: `archon/ux/markdown.py`
- Test: `tests/test_markdown_render.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_markdown_render.py
"""Markdown to ANSI rendering tests."""

from archon.ux.markdown import render_markdown


def test_render_heading():
    result = render_markdown("## Changes Made")
    assert "Changes Made" in result
    assert "\033[1m" in result  # bold


def test_render_bold():
    result = render_markdown("This is **bold** text")
    assert "\033[1m" in result
    assert "bold" in result


def test_render_inline_code():
    result = render_markdown("Use `pytest` to run tests")
    assert "pytest" in result
    assert "\033[" in result  # some styling applied


def test_render_code_block():
    md = '```python\nprint("hello")\n```'
    result = render_markdown(md)
    assert "print" in result
    assert "hello" in result


def test_render_list():
    md = "- item one\n- item two\n- item three"
    result = render_markdown(md)
    assert "item one" in result
    assert "item two" in result


def test_render_numbered_list():
    md = "1. first\n2. second"
    result = render_markdown(md)
    assert "first" in result


def test_render_plain_text_unchanged():
    result = render_markdown("Just plain text here.")
    assert "Just plain text here." in result


def test_render_empty():
    assert render_markdown("") == ""


def test_no_color_mode():
    result = render_markdown("**bold**", use_color=False)
    assert "\033[" not in result
    assert "bold" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_markdown_render.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement markdown renderer**

```python
# archon/ux/markdown.py
"""Minimal markdown-to-ANSI renderer for terminal display."""

from __future__ import annotations

import re

ANSI_BOLD = "\033[1m"
ANSI_DIM = "\033[2m"
ANSI_ITALIC = "\033[3m"
ANSI_CODE_INLINE = "\033[36m"       # cyan for inline code
ANSI_CODE_BLOCK = "\033[38;5;248m"  # light gray for code blocks
ANSI_HEADING = "\033[1;4m"          # bold + underline
ANSI_BULLET = "\033[33m"            # yellow for bullet markers
ANSI_RESET = "\033[0m"


def render_markdown(text: str, *, use_color: bool = True) -> str:
    """Render markdown text with ANSI formatting for terminal display."""
    if not text:
        return ""

    if not use_color:
        # Strip markdown syntax but keep text
        return _strip_markdown(text)

    lines = text.split("\n")
    output: list[str] = []
    in_code_block = False
    code_lines: list[str] = []

    for line in lines:
        # Code block fence
        if line.strip().startswith("```"):
            if in_code_block:
                # End code block
                block = "\n".join(code_lines)
                output.append(f"{ANSI_CODE_BLOCK}{block}{ANSI_RESET}")
                code_lines = []
                in_code_block = False
            else:
                in_code_block = True
            continue

        if in_code_block:
            code_lines.append(f"  {line}")
            continue

        # Headings
        heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading_match:
            text_content = heading_match.group(2)
            output.append(f"{ANSI_HEADING}{text_content}{ANSI_RESET}")
            continue

        # Unordered list items
        list_match = re.match(r"^(\s*)[-*]\s+(.+)$", line)
        if list_match:
            indent = list_match.group(1)
            content = _render_inline(list_match.group(2))
            output.append(f"{indent}{ANSI_BULLET}•{ANSI_RESET} {content}")
            continue

        # Numbered list items
        num_match = re.match(r"^(\s*)(\d+)[.)]\s+(.+)$", line)
        if num_match:
            indent = num_match.group(1)
            num = num_match.group(2)
            content = _render_inline(num_match.group(3))
            output.append(f"{indent}{ANSI_BULLET}{num}.{ANSI_RESET} {content}")
            continue

        # Regular line — apply inline formatting
        output.append(_render_inline(line))

    # Handle unclosed code block
    if code_lines:
        block = "\n".join(code_lines)
        output.append(f"{ANSI_CODE_BLOCK}{block}{ANSI_RESET}")

    return "\n".join(output)


def _render_inline(text: str) -> str:
    """Apply inline markdown formatting (bold, italic, code)."""
    # Inline code (backticks) — must come first to avoid nested processing
    text = re.sub(r"`([^`]+)`", f"{ANSI_CODE_INLINE}\\1{ANSI_RESET}", text)
    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", f"{ANSI_BOLD}\\1{ANSI_RESET}", text)
    # Italic
    text = re.sub(r"\*(.+?)\*", f"{ANSI_ITALIC}\\1{ANSI_RESET}", text)
    return text


def _strip_markdown(text: str) -> str:
    """Remove markdown syntax, keep plain text."""
    lines = text.split("\n")
    output: list[str] = []
    in_code_block = False
    for line in lines:
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            output.append(f"  {line}")
            continue
        line = re.sub(r"^#{1,6}\s+", "", line)
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        line = re.sub(r"\*(.+?)\*", r"\1", line)
        line = re.sub(r"`([^`]+)`", r"\1", line)
        output.append(line)
    return "\n".join(output)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_markdown_render.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add archon/ux/markdown.py tests/test_markdown_render.py
git commit -m "feat: add ANSI markdown renderer for terminal output"
```

### Task 4: Wire Markdown Rendering into Response Output

**Files:**
- Modify: `archon/cli_ui.py` (update `_format_chat_response`)

- [ ] **Step 1: Update response formatting**

Replace `_format_chat_response` in `archon/cli_ui.py:70-80`:

```python
def _format_chat_response(text: str) -> str:
    """Format assistant output with markdown rendering."""
    from archon.ux.markdown import render_markdown

    body = text or "(empty response)"
    rendered = render_markdown(body)
    lines = rendered.splitlines() or [rendered]

    if len(lines) == 1:
        return f"\n{ANSI_PROMPT_ARCHON}archon>{ANSI_RESET} {lines[0]}\n"

    indent = " " * 8
    output = [f"\n{ANSI_PROMPT_ARCHON}archon>{ANSI_RESET} {lines[0]}"]
    output.extend(f"{indent}{line}" for line in lines[1:])
    output.append("")
    return "\n".join(output)
```

- [ ] **Step 2: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add archon/cli_ui.py
git commit -m "feat: wire markdown rendering into chat response formatting"
```

---

## Chunk 3: Single-Keypress Approval Flow

**Goal:** Replace `/approve` + Enter (6 keystrokes) with `y` (1 keypress). Add per-tool "always allow" memory. Use the same box-drawn style as tool calls for visual consistency.

**Target rendering:**
```
╭─ Bash (requires approval)
│  rm -rf /tmp/old-builds
│
╰─ Allow? [y]es / [n]o / [a]lways allow Bash
```

User presses `y` → command runs immediately, no Enter needed.
User presses `a` → all future Bash calls auto-approved for this session.

### Task 5: Single-Keypress Approval Prompt

**Files:**
- Create: `archon/ux/approval.py`
- Test: `tests/test_approval_ux.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_approval_ux.py
"""Approval UX tests."""

from archon.ux.approval import format_approval_prompt, ApprovalMemory


def test_format_approval_prompt():
    result = format_approval_prompt("shell", {"command": "rm -rf /tmp/old"})
    assert "╭" in result
    assert "Bash" in result
    assert "rm -rf" in result
    assert "y" in result.lower() and "n" in result.lower()


def test_approval_memory_initially_empty():
    mem = ApprovalMemory()
    assert not mem.is_always_allowed("shell")


def test_approval_memory_always_allow():
    mem = ApprovalMemory()
    mem.set_always_allow("shell")
    assert mem.is_always_allowed("shell")
    assert not mem.is_always_allowed("write_file")


def test_approval_memory_reset():
    mem = ApprovalMemory()
    mem.set_always_allow("shell")
    mem.reset()
    assert not mem.is_always_allowed("shell")
```

- [ ] **Step 2: Implement approval module**

```python
# archon/ux/approval.py
"""Single-keypress approval prompt with per-tool memory."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from archon.ux.tool_display import format_tool_call, ToolDisplayConfig, _c, ANSI_BOX, ANSI_META


@dataclass
class ApprovalMemory:
    """Tracks per-tool "always allow" decisions for the session."""
    _allowed: set[str] = field(default_factory=set)

    def is_always_allowed(self, tool_name: str) -> bool:
        return tool_name in self._allowed

    def set_always_allow(self, tool_name: str) -> None:
        self._allowed.add(tool_name)

    def reset(self) -> None:
        self._allowed.clear()


def format_approval_prompt(tool_name: str, args: dict) -> str:
    """Format a box-drawn approval prompt for a dangerous tool call."""
    from archon.ux.tool_display import _human_tool_name
    human = _human_tool_name(tool_name)
    header = format_tool_call(tool_name, args)
    footer = f"╰─ Allow? [y]es / [n]o / [a]lways allow {human}"
    return f"{header}\n│\n{footer}"


def read_single_key() -> str:
    """Read a single keypress without requiring Enter (Unix only)."""
    try:
        import tty
        import termios
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        return ch.lower()
    except Exception:
        # Fallback to readline
        return input().strip().lower()[:1]
```

- [ ] **Step 3: Wire into CLI**

In `archon/cli_interactive_commands.py`, replace the current `confirm_for_terminal_session` flow:

```python
# When a DANGEROUS command is detected:
from archon.ux.approval import format_approval_prompt, read_single_key

# Check approval memory first
if approval_memory.is_always_allowed(tool_name):
    return True  # auto-approved

# Show prompt
prompt = format_approval_prompt(tool_name, tool_args)
sys.stderr.write(f"\n{prompt}\n")
sys.stderr.flush()

key = read_single_key()
if key == "y":
    return True
elif key == "a":
    approval_memory.set_always_allow(tool_name)
    return True
elif key == "n":
    return False
```

This replaces the current `/approve` queue-and-wait mechanism for terminal sessions. Keep the existing mechanism as fallback for non-TTY environments (Telegram, etc.).

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add archon/ux/approval.py archon/cli_interactive_commands.py tests/test_approval_ux.py
git commit -m "feat: single-keypress approval with per-tool always-allow memory"
```

---

## Chunk 4: Session Continuity — Wire Compressor & Distiller

**Goal:** Connect the existing compressor and distiller modules into the agent lifecycle. LLM-powered compaction replaces mechanical bullet points. Session end triggers distillation.

### Task 6: LLM-Powered Compaction

**Files:**
- Modify: `archon/memory.py` (add `compact_history_llm()`)
- Modify: `archon/agent.py` (prefer LLM compaction when LLM available)
- Test: `tests/test_compressor.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_compressor.py (append)

def test_compact_history_llm():
    from archon.memory import compact_history_llm

    messages = [
        {"role": "user", "content": "deploy korami"},
        {"role": "assistant", "content": "Deploying now."},
    ]

    def fake_llm(prompt, system=""):
        return "User requested korami deployment. Assistant ran bun build and vercel --prod."

    result = compact_history_llm(messages, llm_fn=fake_llm, summary_id="test-llm-1")
    assert result["layer"] == "session"
    assert "korami" in result["content"]
    assert result["path"]


def test_compact_history_llm_fallback_on_error():
    from archon.memory import compact_history_llm

    messages = [
        {"role": "user", "content": "test"},
        {"role": "assistant", "content": "reply"},
    ]

    def broken_llm(prompt, system=""):
        raise RuntimeError("LLM unavailable")

    # Should fall back to mechanical compaction, not crash
    result = compact_history_llm(messages, llm_fn=broken_llm, summary_id="test-fallback")
    assert result["path"]
```

- [ ] **Step 2: Implement compact_history_llm**

Add to `archon/memory.py`:

```python
def compact_history_llm(
    messages: list[dict],
    *,
    llm_fn,
    layer: str = "session",
    summary_id: str = "latest",
) -> dict:
    """LLM-powered compaction. Falls back to mechanical on failure."""
    from archon.compressor import (
        COMPRESSION_SYSTEM_PROMPT,
        build_compression_prompt,
        parse_compression_result,
    )

    try:
        prompt = build_compression_prompt(messages)
        llm_output = llm_fn(prompt, COMPRESSION_SYSTEM_PROMPT)
        result = parse_compression_result(llm_output, layer=layer, summary_id=summary_id)
    except Exception:
        return compact_history(messages, layer=layer, summary_id=summary_id)

    # Write to compaction path
    sub_dir = "sessions" if layer == "session" else "tasks"
    dest = MEMORY_DIR / "compactions" / sub_dir / f"{summary_id}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(result["content"], encoding="utf-8")
    result["path"] = str(dest.relative_to(MEMORY_DIR))
    return result
```

- [ ] **Step 3: Wire into agent**

In `archon/agent.py`, where `compact_history()` is called during trimming, prefer LLM version:

```python
# Where compact_history is currently called:
if self.llm:
    def _llm_fn(prompt, system=""):
        # Use a lightweight call — not the full tool-enabled path
        return self.llm.complete_simple(prompt, system_prompt=system)
    try:
        artifact = memory_store.compact_history_llm(dropped, llm_fn=_llm_fn, summary_id=sid)
    except Exception:
        artifact = memory_store.compact_history(dropped, summary_id=sid)
else:
    artifact = memory_store.compact_history(dropped, summary_id=sid)
```

Note: Requires `complete_simple()` on LLM adapter. If not present, check existing interface. If the LLM adapter only has streaming, use `compact_history()` as fallback. Do NOT block on this — mechanical fallback is fine.

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add archon/memory.py archon/agent.py tests/test_compressor.py
git commit -m "feat: wire LLM-powered compaction into history trimming with mechanical fallback"
```

### Task 7: Session-End Distillation

**Files:**
- Create: `archon/session_lifecycle.py`
- Modify: `archon/cli_interactive_commands.py` (trigger on session end)
- Test: `tests/test_session_lifecycle.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_session_lifecycle.py
"""Session lifecycle tests."""

from archon.session_lifecycle import distill_session, build_session_greeting


def test_distill_session():
    messages = [
        {"role": "user", "content": "set up browser-use"},
        {"role": "assistant", "content": "Installed chromium and configured env."},
        {"role": "user", "content": "now run it"},
        {"role": "assistant", "content": "Running browser-use script successfully."},
    ]

    def fake_llm(prompt, system=""):
        return "FACT|high|project:browser-use|needs chromium|projects/browser-use.md"

    result = distill_session(messages, llm_fn=fake_llm)
    assert len(result.inbox_items) == 1
    assert result.inbox_items[0]["kind"] == "fact"


def test_distill_session_too_short():
    result = distill_session(
        [{"role": "user", "content": "hi"}],
        llm_fn=lambda p, s="": "NONE",
    )
    assert result.inbox_items == []


def test_distill_session_llm_failure():
    def broken(prompt, system=""):
        raise RuntimeError("down")
    result = distill_session(
        [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}] * 3,
        llm_fn=broken,
    )
    assert result.inbox_items == []
    assert result.error


def test_build_greeting_with_compaction(tmp_path):
    comp_dir = tmp_path / "compactions" / "sessions"
    comp_dir.mkdir(parents=True)
    (comp_dir / "s1.md").write_text("# Session Compaction Summary\n\nDeployed korami to Vercel.\n")
    greeting = build_session_greeting(memory_dir=tmp_path)
    assert "korami" in greeting.lower() or "deployed" in greeting.lower()


def test_build_greeting_empty(tmp_path):
    assert build_session_greeting(memory_dir=tmp_path) == ""
```

- [ ] **Step 2: Implement session lifecycle**

```python
# archon/session_lifecycle.py
"""Session start/end lifecycle hooks."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SessionEndResult:
    inbox_items: list[dict] = field(default_factory=list)
    error: str = ""


def distill_session(
    messages: list[dict],
    *,
    llm_fn,
    min_messages: int = 4,
) -> SessionEndResult:
    """Extract structured learnings from a session conversation."""
    if len(messages) < min_messages:
        return SessionEndResult()

    from archon.distiller import build_distillation_prompt, parse_distillation_output

    try:
        prompt = build_distillation_prompt(messages)
        llm_output = llm_fn(prompt, "Extract structured learnings from this conversation.")
        items = parse_distillation_output(llm_output)
        return SessionEndResult(inbox_items=items)
    except Exception as exc:
        return SessionEndResult(error=str(exc))


def build_session_greeting(*, memory_dir: Path | None = None) -> str:
    """Build a one-line greeting from the last session's compaction."""
    from archon.config import MEMORY_DIR

    mem_dir = memory_dir or MEMORY_DIR
    comp_dir = mem_dir / "compactions" / "sessions"
    if not comp_dir.exists():
        return ""

    files = sorted(comp_dir.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        return ""

    text = files[0].read_text(errors="replace").strip()
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return f"Last session: {line[:120]}"
    return ""
```

- [ ] **Step 3: Wire into CLI**

In `archon/cli_interactive_commands.py`:

1. On REPL startup (after banner):
```python
greeting = build_session_greeting()
if greeting:
    click_echo_fn(f"{ANSI_DIM}{greeting}{ANSI_RESET}")
```

2. On `/clear`, `/reset`, or Ctrl-D exit, before clearing:
```python
if len(agent.history) >= 4 and agent.llm:
    result = distill_session(agent.history, llm_fn=_make_simple_llm(agent))
    for item in result.inbox_items:
        try:
            memory_store.inbox_add(**item)
        except Exception:
            pass
    if result.inbox_items:
        click_echo_fn(f"{ANSI_DIM}Distilled {len(result.inbox_items)} learning(s) → memory inbox{ANSI_RESET}")
```

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add archon/session_lifecycle.py archon/cli_interactive_commands.py tests/test_session_lifecycle.py
git commit -m "feat: wire session distillation and greeting into REPL lifecycle"
```

---

## Chunk 5: Plan Mode + /undo

**Goal:** Add think-before-act with `/plan` and safe rollback with `/undo`.

### Task 8: Plan Mode

**Files:**
- Create: `archon/plan_mode.py`
- Modify: `archon/cli_repl_commands.py`
- Modify: `archon/cli_interactive_commands.py`
- Test: `tests/test_plan_mode.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_plan_mode.py
"""Plan mode tests."""

from archon.plan_mode import PlanModeState, PLAN_ALLOWED_TOOLS


def test_enter_exit():
    pm = PlanModeState()
    pm.enter("fix auth bug")
    assert pm.active
    assert pm.goal == "fix auth bug"
    pm.exit()
    assert not pm.active


def test_allowed_tools_are_read_only():
    assert "read_file" in PLAN_ALLOWED_TOOLS
    assert "list_dir" in PLAN_ALLOWED_TOOLS
    assert "memory_read" in PLAN_ALLOWED_TOOLS
    assert "memory_lookup" in PLAN_ALLOWED_TOOLS
    assert "web_search" in PLAN_ALLOWED_TOOLS
    # Must NOT include write/destructive tools
    assert "shell" not in PLAN_ALLOWED_TOOLS
    assert "write_file" not in PLAN_ALLOWED_TOOLS
    assert "edit_file" not in PLAN_ALLOWED_TOOLS


def test_guidance_contains_goal():
    pm = PlanModeState()
    pm.enter("fix the login flow")
    guidance = pm.build_guidance()
    assert "fix the login flow" in guidance
    assert "read" in guidance.lower() or "plan" in guidance.lower()
```

- [ ] **Step 2: Implement and wire**

Create `archon/plan_mode.py` with `PlanModeState`, `PLAN_ALLOWED_TOOLS`, `PLAN_MODE_GUIDANCE`.

Add `/plan <goal>` and `/go` to slash commands.

Enforce via session profile with `allowed_tools=PLAN_ALLOWED_TOOLS`. Inject plan guidance into system prompt.

- [ ] **Step 3: Run full test suite and commit**

```bash
git add archon/plan_mode.py archon/cli_repl_commands.py archon/cli_interactive_commands.py tests/test_plan_mode.py
git commit -m "feat: add /plan and /go commands with read-only exploration mode"
```

### Task 9: /undo Command

**Files:**
- Modify: `archon/cli_repl_commands.py`
- Modify: `archon/execution/turn_executor.py` (track file mods)

- [ ] **Step 1: Implement file tracking + /undo**

In turn executor, after successful `write_file` or `edit_file`:
```python
modified = getattr(agent, "_last_modified_files", [])
modified.append(path)
agent._last_modified_files = modified[-20:]
```

In slash commands:
```python
def handle_undo_command(agent, text: str) -> tuple[bool, str]:
    if (text or "").strip().lower() != "/undo":
        return False, ""
    files = getattr(agent, "_last_modified_files", [])
    if not files:
        return True, "No file changes to undo."
    path = files.pop()
    import subprocess
    try:
        subprocess.run(["git", "checkout", "--", path], check=True, capture_output=True)
        return True, f"Reverted: {path}"
    except subprocess.CalledProcessError:
        return True, f"Could not revert {path} (not in git?)"
```

- [ ] **Step 2: Run full test suite and commit**

```bash
git add archon/cli_repl_commands.py archon/execution/turn_executor.py
git commit -m "feat: add /undo with git-backed file revert and modification tracking"
```

---

## Chunk 6: Cost Display + Turn Stats Cleanup

**Goal:** Show estimated cost per turn (like Claude Code) and clean up the turn stats line.

### Task 10: Cost Estimation

**Files:**
- Modify: `archon/cli_ui.py` (update `_format_turn_stats`)
- Modify: `archon/config.py` (add cost-per-token config)

- [ ] **Step 1: Add cost config**

In `archon/config.py`, add to `LLMConfig`:
```python
cost_per_input_token: float = 0.0    # USD, 0 = don't show
cost_per_output_token: float = 0.0
```

Default costs for known models (set during config load):
```python
_MODEL_COSTS = {
    "claude-sonnet-4-6": (0.003 / 1000, 0.015 / 1000),    # $3/$15 per MTok
    "claude-opus-4-6": (0.015 / 1000, 0.075 / 1000),       # $15/$75 per MTok
    "gpt-4o": (0.0025 / 1000, 0.01 / 1000),                # $2.5/$10 per MTok
}
```

- [ ] **Step 2: Update turn stats format**

Replace `_format_turn_stats` in `cli_ui.py`:

```
  2.5s | $0.03 | 450↑ 780↓ | session: $0.42
```

Instead of:
```
  2.5s | 450 in | 780 out | session: 1,230 tokens | phase: shell | route: fast
```

The cost is the primary info. Token counts use compact arrows. Phase/route info dropped from default display (available via `/status`).

If cost config is 0 (unknown model), fall back to token counts.

- [ ] **Step 3: Run full test suite and commit**

```bash
git add archon/cli_ui.py archon/config.py
git commit -m "feat: add per-turn cost estimation and clean up turn stats display"
```

---

## Priority and Dependencies

```
Chunk 1 (Tool Display)     ← No deps, HIGHEST IMPACT
Chunk 2 (Markdown Output)  ← No deps, HIGH IMPACT
Chunk 3 (Approval Flow)    ← Depends on Chunk 1 (uses same box style)
Chunk 4 (Session Lifecycle) ← No deps, uses existing compressor.py/distiller.py
Chunk 5 (Plan + Undo)      ← No deps
Chunk 6 (Cost Display)     ← No deps
```

**Recommended order:** 1 → 2 → 3 → 4 → 5 → 6

Chunks 1+2 can be parallelized (different files). Chunks 4+5+6 are independent of each other.

---

## Test Coverage Targets

| Module | Tests |
|--------|-------|
| `ux/tool_display.py` | 12 |
| `ux/markdown.py` | 9 |
| `ux/approval.py` | 4 |
| `session_lifecycle.py` | 5 |
| `plan_mode.py` | 3 |
| Cost estimation | 2 |
| /undo | 2 |
| **Total new** | **~37** |

All existing 744 tests must continue to pass.

---

## What This Achieves

After implementing all 6 chunks, a typical Archon interaction looks like:

```
Archon v0.x.x | anthropic/claude-sonnet-4-6
Last session: Fixed auth bug in login_handler.py

you> fix the failing test in test_auth.py

╭─ Read tests/test_auth.py
│
│  def test_login_rejects_empty_password():
│      result = login("user", "")
│      assert result.status == "rejected"
│
╰─ 45 lines

╭─ Read archon/auth/login.py
│
│  def login(username, password):
│      if not password:
│          return AuthResult("error")  # bug: should be "rejected"
│
╰─ 32 lines

╭─ Edit archon/auth/login.py
│  return AuthResult("error")  →  return AuthResult("rejected")
╰─ 1 change

╭─ Bash
│  pytest tests/test_auth.py -x -q
│
│  1 passed in 0.3s
│
╰─ exit 0, 0.5s

archon> ## Fixed

        The test expected `status == "rejected"` but `login()` was returning
        `"error"` for empty passwords. Changed line 15 in `auth/login.py`.

        All tests pass.

  3.2s | $0.02 | 1,200↑ 450↓ | session: $0.02

Distilled 1 learning → memory inbox
```

That's Claude Code quality.
