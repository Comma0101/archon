# Assistant UX Clarity and Deep Research Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make Archon easier to use and easier to trust by simplifying the visible shell UX, grounding skills/MCP/plugins in live runtime state, mirroring Telegram activity cleanly into terminal, and adding a native Google Deep Research background job path.

**Architecture:** Keep the existing Agent/control plane as the fast path, but add a small shared activity-event layer, live runtime command metadata, and a native async research backend. Do not introduce a full dashboard or another orchestration framework; integrate Deep Research through the existing `job` lane and normalized job-summary system.

**Tech Stack:** Python 3.11+, click/readline terminal REPL, existing Archon control plane, `google-genai` Gemini client, pytest.

---

### Task 1: Replace Static MCP/Plugin Examples With Live Runtime Metadata

**Files:**
- Modify: `archon/cli_commands.py`
- Modify: `archon/cli.py`
- Modify: `archon/cli_interactive_commands.py`
- Test: `tests/test_cli.py`

**Step 1: Write the failing tests**

```python
def test_build_slash_subvalues_uses_live_mcp_server_names():
    cfg = Config()
    cfg.mcp.servers = {"exa": MCPServerConfig(enabled=True, mode="read_only", transport="stdio")}

    subvalues = build_slash_subvalues(MODEL_CATALOG, cfg)

    assert ("show exa", "Show one MCP server config") in subvalues["/mcp"]
    assert all("docs" not in value for value, _desc in subvalues["/mcp"])


def test_plugins_subvalues_use_live_mcp_plugin_names():
    cfg = Config()
    cfg.mcp.servers = {"exa": MCPServerConfig(enabled=True, mode="read_only", transport="stdio")}

    subvalues = build_slash_subvalues(MODEL_CATALOG, cfg)

    assert ("show mcp:exa", "Show one MCP plugin") in subvalues["/plugins"]
    assert all("mcp:docs" not in value for value, _desc in subvalues["/plugins"])
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli.py -q -k "live_mcp_server_names or live_mcp_plugin_names"`
Expected: FAIL because `build_slash_subvalues()` is still static and hardcodes `docs`.

**Step 3: Write minimal implementation**

- Change `build_slash_subvalues()` to accept optional runtime config/server/plugin names.
- Build `/mcp` and `/plugins` subvalues from live configured MCP servers when available.
- Keep safe generic fallbacks only for non-runtime-specific verbs.
- Update CLI wiring so interactive chat uses agent-config-backed slash subvalues instead of module-global static examples.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cli.py -q -k "live_mcp_server_names or live_mcp_plugin_names"`
Expected: PASS.

**Step 5: Commit**

```bash
git add archon/cli_commands.py archon/cli.py archon/cli_interactive_commands.py tests/test_cli.py
git commit -m "fix: use live runtime names in slash metadata"
```

### Task 2: Echo Picker-Selected Slash Commands Back Into The Transcript

**Files:**
- Modify: `archon/cli_interactive_commands.py`
- Test: `tests/test_cli.py`

**Step 1: Write the failing test**

```python
def test_bare_slash_echoes_selected_command_before_execution(monkeypatch):
    outputs = _run_local_command_session(
        agent,
        ["/", "quit"],
        pick_slash_command_fn=lambda: "/status",
    )

    plain = _plain_outputs(outputs)
    assert any("you> /status" in text for text in plain)
    assert any(text.startswith("Status:") for text in plain)
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli.py -q -k "bare_slash_echoes_selected_command"`
Expected: FAIL because `/` currently runs the picked command without echoing it.

**Step 3: Write minimal implementation**

- After picker selection, print the selected command in the same user-facing style as typed input before handling it.
- Keep existing picker behavior unchanged.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cli.py -q -k "bare_slash_echoes_selected_command"`
Expected: PASS.

**Step 5: Commit**

```bash
git add archon/cli_interactive_commands.py tests/test_cli.py
git commit -m "fix: echo picked slash commands in terminal transcript"
```

### Task 3: Add A Shared Readline-Safe Terminal Activity Feed

**Files:**
- Create: `archon/ux/events.py`
- Create: `archon/ux/terminal_feed.py`
- Modify: `archon/cli_ui.py`
- Modify: `archon/cli_interactive_commands.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_terminal_feed.py`

**Step 1: Write the failing tests**

```python
def test_terminal_feed_renders_notice_and_restores_prompt():
    feed = TerminalActivityFeed(write_fn=buf.write, flush_fn=lambda: None)
    feed.set_prompt_state(prompt="you> ", current_input="use rese")

    feed.emit(ActivityEvent(kind="telegram.received", message="[telegram] message received"))

    rendered = buf.getvalue()
    assert "[telegram] message received" in rendered
    assert "you> use rese" in rendered


def test_terminal_feed_formats_skill_activation_notice():
    event = ActivityEvent(kind="skill.activated", message="[skill] auto-activated: researcher")
    assert format_activity_notice(event) == "[skill] auto-activated: researcher"
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_terminal_feed.py tests/test_cli.py -q -k "terminal_feed or skill_activation_notice"`
Expected: FAIL because no shared feed exists yet.

**Step 3: Write minimal implementation**

- Create a tiny `ActivityEvent` dataclass and terminal-feed renderer.
- Renderer responsibilities:
  - print notice above current line
  - redraw prompt
  - redraw in-progress input
- Keep it text-only and lightweight.
- Update interactive chat loop to keep prompt state current and expose a feed hook.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_terminal_feed.py tests/test_cli.py -q -k "terminal_feed or skill_activation_notice"`
Expected: PASS.

**Step 5: Commit**

```bash
git add archon/ux/events.py archon/ux/terminal_feed.py archon/cli_ui.py archon/cli_interactive_commands.py tests/test_terminal_feed.py tests/test_cli.py
git commit -m "feat: add readline-safe terminal activity feed"
```

### Task 4: Mirror Telegram Activity Into Terminal As Compact Notices

**Files:**
- Modify: `archon/adapters/telegram.py`
- Modify: `archon/cli_runtime.py`
- Test: `tests/test_telegram_adapter.py`

**Step 1: Write the failing tests**

```python
def test_telegram_message_emits_terminal_notice():
    notices = []
    adapter = make_adapter(activity_sink=notices.append)

    adapter._emit_activity_event("telegram.received", {"chat_id": 123})

    assert notices[-1].message.startswith("[telegram] message received")


def test_telegram_reply_emits_compact_terminal_notice_not_tool_dump():
    notices = []
    adapter = make_adapter(activity_sink=notices.append)

    adapter._emit_activity_event("telegram.replied", {"chat_id": 123})

    assert notices[-1].message == "[telegram] replied"
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_telegram_adapter.py -q -k "terminal_notice or compact_terminal_notice"`
Expected: FAIL because Telegram currently has no shared terminal activity sink.

**Step 3: Write minimal implementation**

- Add an optional activity sink to the Telegram adapter.
- Emit compact notices for:
  - message received
  - route=job started
  - approval blocked/approved
  - replied
- Do not mirror raw tool calls/results.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_telegram_adapter.py -q -k "terminal_notice or compact_terminal_notice"`
Expected: PASS.

**Step 5: Commit**

```bash
git add archon/adapters/telegram.py archon/cli_runtime.py tests/test_telegram_adapter.py
git commit -m "feat: mirror telegram activity into terminal notices"
```

### Task 5: Auto-Activate Skills From Clear Natural-Language Requests

**Files:**
- Modify: `archon/control/skills.py`
- Modify: `archon/cli_interactive_commands.py`
- Modify: `archon/adapters/telegram.py`
- Modify: `archon/prompt.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_telegram_adapter.py`
- Test: `tests/test_agent.py`

**Step 1: Write the failing tests**

```python
def test_terminal_auto_activates_researcher_skill_from_clear_request():
    outputs = _run_local_command_session(agent, ["use researcher skill to research LA restaurants", "quit"])
    plain = _plain_outputs(outputs)
    assert any("[skill] auto-activated: researcher" in text for text in plain)


def test_telegram_auto_activates_skill_for_clear_request():
    sent = run_telegram_message("use coder skill to inspect the repo")
    assert any("[skill] auto-activated: coder" in text for _, text in sent)
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli.py tests/test_telegram_adapter.py tests/test_agent.py -q -k "auto_activates"`
Expected: FAIL because natural-language skill switching is not implemented.

**Step 3: Write minimal implementation**

- Add a small high-confidence skill detector in `archon/control/skills.py`.
- Match only explicit phrases like `use <skill> skill`, `switch to <skill>`, `act as <skill>`.
- Apply session-scoped skill activation before agent execution on both terminal and Telegram surfaces.
- Emit a visible activity event for successful activation.
- Extend prompt/runtime capability context so the agent sees the active skill name explicitly.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cli.py tests/test_telegram_adapter.py tests/test_agent.py -q -k "auto_activates"`
Expected: PASS.

**Step 5: Commit**

```bash
git add archon/control/skills.py archon/cli_interactive_commands.py archon/adapters/telegram.py archon/prompt.py tests/test_cli.py tests/test_telegram_adapter.py tests/test_agent.py
git commit -m "feat: auto-activate explicit session skills"
```

### Task 6: Ground Capability Answers In Live Runtime State And Redact Secrets

**Files:**
- Create: `archon/security/redaction.py`
- Modify: `archon/agent.py`
- Modify: `archon/prompt.py`
- Test: `tests/test_agent.py`

**Step 1: Write the failing tests**

```python
def test_tool_result_redacts_api_keys_before_terminal_render_and_history():
    secret = "OPENAI_API_KEY=sk-test-secret-value"
    rendered, stored = render_and_capture_tool_result(secret)

    assert "sk-test-secret-value" not in rendered
    assert "sk-test-secret-value" not in stored
    assert "[REDACTED]" in rendered


def test_runtime_capability_summary_uses_live_skills_and_mcp_servers():
    agent.config.mcp.servers = {"exa": MCPServerConfig(enabled=True, mode="read_only", transport="stdio")}
    prompt = build_runtime_capability_summary(agent.config)

    assert "exa" in prompt
    assert "researcher" in prompt
    assert "memory_curator" in prompt
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_agent.py -q -k "redacts_api_keys or runtime_capability_summary"`
Expected: FAIL because tool results are not redacted and no live capability summary exists.

**Step 3: Write minimal implementation**

- Add conservative redaction helpers for obvious secret patterns.
- Apply redaction before:
  - terminal tool-result printing
  - truncation into history
- Add a small runtime capability summary builder used in turn prompt construction so assistant capability answers are grounded in live config/registries.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_agent.py -q -k "redacts_api_keys or runtime_capability_summary"`
Expected: PASS.

**Step 5: Commit**

```bash
git add archon/security/redaction.py archon/agent.py archon/prompt.py tests/test_agent.py
git commit -m "fix: ground capability state and redact secret-like output"
```

### Task 7: Add Google Deep Research Config And Client Wrapper

**Files:**
- Modify: `archon/config.py`
- Create: `archon/research/google_deep_research.py`
- Test: `tests/test_config.py`
- Test: `tests/test_research.py`

**Step 1: Write the failing tests**

```python
def test_load_config_parses_google_deep_research_settings(tmp_path, monkeypatch):
    cfg = load_config_from_text(
        """
        [research.google_deep_research]
        enabled = true
        model = "gemini-2.5-deep-research"
        timeout_minutes = 20
        """
    )

    assert cfg.research.google_deep_research.enabled is True
    assert cfg.research.google_deep_research.model == "gemini-2.5-deep-research"


def test_google_deep_research_client_starts_background_interaction(fake_google_client):
    client = GoogleDeepResearchClient(fake_google_client)
    result = client.start_research("Research LA restaurant market")

    assert result.interaction_id == "int-123"
    assert result.status == "running"
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py tests/test_research.py -q -k "google_deep_research"`
Expected: FAIL because no research config/client exists.

**Step 3: Write minimal implementation**

- Add a `research` config section with a nested `google_deep_research` config.
- Create a dedicated wrapper around the Gemini Interactions API.
- Encode the official constraints directly in the wrapper:
  - `background=True`
  - `store=True`
  - no MCP/custom-tool wiring
- Keep the client wrapper testable by dependency injection.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py tests/test_research.py -q -k "google_deep_research"`
Expected: PASS.

**Step 5: Commit**

```bash
git add archon/config.py archon/research/google_deep_research.py tests/test_config.py tests/test_research.py
git commit -m "feat: add google deep research config and client"
```

### Task 8: Persist Deep Research Jobs And Integrate Them Into `/jobs`

**Files:**
- Create: `archon/research/models.py`
- Create: `archon/research/store.py`
- Modify: `archon/control/jobs.py`
- Modify: `archon/cli_repl_commands.py`
- Test: `tests/test_research.py`
- Test: `tests/test_cli.py`

**Step 1: Write the failing tests**

```python
def test_research_job_summary_appears_in_jobs_list(tmp_path, monkeypatch):
    store = ResearchJobStore(tmp_path)
    store.save(job_id="research:abc", status="running", summary="LA restaurant market")

    msg = handle_jobs_command(None, "/jobs")[1]
    assert "research:abc" in msg


def test_job_command_loads_one_research_job_summary(tmp_path, monkeypatch):
    store = ResearchJobStore(tmp_path)
    store.save(job_id="research:abc", status="done", summary="Completed")

    handled, msg = handle_job_command(None, "/job research:abc")
    assert handled is True
    assert "job_id: research:abc" in msg
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_research.py tests/test_cli.py -q -k "research_job"`
Expected: FAIL because `/jobs` and `/job` only know workers/calls.

**Step 3: Write minimal implementation**

- Add a tiny research job store under XDG state.
- Normalize research jobs through the existing `JobSummary` path.
- Extend `/jobs` and `/job` to include research jobs alongside workers/calls.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_research.py tests/test_cli.py -q -k "research_job"`
Expected: PASS.

**Step 5: Commit**

```bash
git add archon/research/models.py archon/research/store.py archon/control/jobs.py archon/cli_repl_commands.py tests/test_research.py tests/test_cli.py
git commit -m "feat: add deep research jobs to shared job surface"
```

### Task 9: Route Broad Research Requests Into Deep Research Jobs

**Files:**
- Modify: `archon/control/orchestrator.py`
- Modify: `archon/agent.py`
- Modify: `archon/cli_interactive_commands.py`
- Modify: `archon/adapters/telegram.py`
- Test: `tests/test_agent.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_telegram_adapter.py`
- Test: `tests/test_research.py`

**Step 1: Write the failing tests**

```python
def test_broad_research_request_starts_google_deep_research_job_when_enabled():
    agent.config.research.google_deep_research.enabled = True
    response = agent.run("Deeply research the LA restaurant market and synthesize competitors")
    assert "Research job started" in response


def test_simple_research_question_stays_on_fast_path():
    lane, reason = _classify_route("What is Exa MCP?")
    assert lane == "fast"
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_agent.py tests/test_research.py tests/test_cli.py tests/test_telegram_adapter.py -q -k "research job started or simple_research_question"`
Expected: FAIL because job-lane research execution does not exist.

**Step 3: Write minimal implementation**

- Add a narrow broad-research detector on top of the current router.
- In hybrid mode, let the agent intercept that route and start a research job instead of immediately falling through to the normal fast chat loop.
- Emit compact activity notices for start/completion events.
- Return a short user-facing message with the research job id and `/jobs` guidance.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_agent.py tests/test_research.py tests/test_cli.py tests/test_telegram_adapter.py -q -k "research job started or simple_research_question"`
Expected: PASS.

**Step 5: Commit**

```bash
git add archon/control/orchestrator.py archon/agent.py archon/cli_interactive_commands.py archon/adapters/telegram.py tests/test_agent.py tests/test_research.py tests/test_cli.py tests/test_telegram_adapter.py
git commit -m "feat: route broad research requests to deep research jobs"
```

### Task 10: Final Verification, Context Sync, And Review

**Files:**
- Modify: `AGENT_CONTEXT.json`
- Optional review touch-ups from findings

**Step 1: Run focused verification**

Run:
- `python -m pytest tests/test_cli.py tests/test_terminal_feed.py tests/test_telegram_adapter.py tests/test_agent.py tests/test_research.py tests/test_config.py -q`

Expected: PASS.

**Step 2: Run full verification**

Run:
- `XDG_STATE_HOME=/tmp/archon-state python -m pytest tests -q`

Expected: PASS.

**Step 3: Sync context**

- Update `AGENT_CONTEXT.json` with:
  - dynamic runtime command metadata
  - terminal activity feed + Telegram compact mirroring
  - skill auto-activation + explicit confirmation
  - secret redaction hardening
  - Google Deep Research native job integration
- Update `total_tests` with the final verified count.

**Step 4: Commit**

```bash
git add AGENT_CONTEXT.json
git commit -m "docs: sync assistant ux clarity context"
```

**Step 5: Request review and finish branch**

- Use `superpowers:requesting-code-review`
- Then use `superpowers:finishing-a-development-branch`
