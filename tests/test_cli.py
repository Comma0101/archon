"""CLI formatting helper tests."""

import re
from types import SimpleNamespace

import pytest

from archon.config import Config, MCPServerConfig, ProfileConfig
from archon.cli import _format_chat_response
from archon.cli import _is_paste_command, _collect_paste_message
from archon.cli import (
    _make_readline_prompt,
    _build_model_set_subvalues,
    _SLASH_SUBVALUES,
    _format_turn_stats,
    _format_session_summary,
    _is_bracketed_paste_start,
    _collect_bracketed_paste,
    _handle_model_command,
    _handle_model_list_command,
    _handle_model_set_command,
    _handle_repl_command,
    _set_calls_enabled_in_toml,
    _slash_completer,
    _run_picker,
    _pick_slash_command,
    _SLASH_COMMANDS,
)
from archon.cli_interactive_commands import chat_cmd as _chat_cmd
from archon.cli_interactive_commands import _tool_spinner_label
from archon.control.hooks import HookBus
from archon.prompt import build_skill_guidance as _build_skill_guidance


class TestCliFormatting:
    @staticmethod
    def _plain(text: str) -> str:
        return re.sub(r"\x1b\[[0-9;]*m", "", text)

    def test_format_chat_response_single_line(self):
        out = self._plain(_format_chat_response("Hello"))
        assert "archon>" in out
        assert "Hello" in out
        assert "\n  " not in out

    def test_format_chat_response_multiline_indents_following_lines(self):
        out = self._plain(_format_chat_response("Line1\nLine2\nLine3"))
        assert "archon> Line1" in out
        assert "\n        Line2" in out
        assert "\n        Line3" in out

    def test_format_chat_response_uses_colored_archon_prompt(self):
        out = _format_chat_response("Hello")
        assert "\x1b[" in out
        assert "\x1b[92;1m" in out  # bright green + bold archon prompt

    def test_make_readline_prompt_wraps_non_printing_ansi_sequences(self):
        prompt = _make_readline_prompt("you>", "\033[93;1m")
        assert prompt == "\x01\033[93;1m\x02you>\x01\033[0m\x02 "

    def test_format_turn_stats(self):
        out = self._plain(_format_turn_stats(1.23, 1234, 56, 1234, 56))
        assert "1.2s" in out
        assert "1,234 in" in out
        assert "56 out" in out
        assert "session: 1,290 tokens" in out

    def test_format_turn_stats_includes_route_state_when_present(self):
        out = self._plain(
            _format_turn_stats(
                1.23,
                1234,
                56,
                1234,
                56,
                route_lane="job",
                route_reason="broad_scope_request",
            )
        )
        assert "route: job" in out
        assert "broad scope request" in out

    def test_format_turn_stats_includes_phase_when_present(self):
        out = self._plain(
            _format_turn_stats(
                1.23,
                1234,
                56,
                1234,
                56,
                phase_label="mcp exa",
            )
        )
        assert "phase: mcp exa" in out

    def test_format_session_summary(self):
        out = self._plain(_format_session_summary(3, 1200, 345))
        assert "Session: 3 turns" in out
        assert "1,200 in" in out
        assert "345 out" in out
        assert "1,545 total tokens" in out

    def test_format_session_summary_includes_route_progress_when_available(self):
        out = self._plain(
            _format_session_summary(
                3,
                1200,
                345,
                route_counts={"fast": 1, "job": 2},
            )
        )
        assert "routes:" in out
        assert "fast=1" in out
        assert "job=2" in out


class TestCliPasteMode:
    def test_is_paste_command_accepts_common_forms(self):
        assert _is_paste_command("/paste")
        assert _is_paste_command("paste")
        assert _is_paste_command(":paste")
        assert not _is_paste_command("pastel")

    def test_collect_paste_message_reads_until_end_marker(self):
        calls = []
        lines = iter(["line 1", "line 2", "/end", "ignored"])

        def fake_input(prompt):
            calls.append(prompt)
            return next(lines)

        msg = _collect_paste_message(fake_input, prompt="PROMPT> ")
        assert msg == "line 1\nline 2"
        assert calls == ["PROMPT> ", "PROMPT> ", "PROMPT> "]

    def test_collect_paste_message_allows_blank_lines(self):
        lines = iter(["alpha", "", "beta", ".end"])
        msg = _collect_paste_message(lambda _p: next(lines), prompt="> ")
        assert msg == "alpha\n\nbeta"

    def test_detects_bracketed_paste_start(self):
        assert _is_bracketed_paste_start("\x1b[200~hello")
        assert not _is_bracketed_paste_start("hello")

    def test_collect_bracketed_paste_multiline(self):
        lines = iter(["line 2", "line 3\x1b[201~"])
        msg = _collect_bracketed_paste("\x1b[200~line 1", lambda _p: next(lines), prompt="...> ")
        assert msg == "line 1\nline 2\nline 3"

    def test_collect_bracketed_paste_single_line(self):
        msg = _collect_bracketed_paste("\x1b[200~hello world\x1b[201~", lambda _p: "", prompt="...> ")
        assert msg == "hello world"


class TestCliCommands:
    @staticmethod
    def _make_local_command_agent():
        cfg = Config()
        cfg.llm.provider = "openai"
        cfg.llm.model = "gpt-5-mini"
        cfg.llm.api_key = "test-key"
        cfg.calls.enabled = True
        cfg.mcp.servers = {
            "docs": MCPServerConfig(enabled=True, mode="read_only", transport="stdio"),
            "build": MCPServerConfig(enabled=False, mode="read_write", transport="stdio"),
        }
        cfg.profiles = {
            "default": ProfileConfig(),
            "safe": ProfileConfig(allowed_tools=["shell", "read_file"], max_mode="review"),
        }
        return SimpleNamespace(
            llm=SimpleNamespace(provider="openai", model="gpt-5-mini"),
            config=cfg,
            policy_profile="safe",
            total_input_tokens=120,
            total_output_tokens=30,
        )

    def test_slash_commands_include_local_shell_status_commands(self):
        names = {name for name, _desc in _SLASH_COMMANDS}
        assert {"/status", "/cost", "/doctor", "/permissions"} <= names

    def test_slash_command_descriptions_group_shell_controls(self):
        descriptions = dict(_SLASH_COMMANDS)
        assert descriptions["/status"] == "Shell: current status"
        assert descriptions["/skills"] == "Shell: skills"
        assert descriptions["/plugins"] == "Shell: plugins"
        assert descriptions["/model"] == "Model: current provider/model"
        assert descriptions["/mcp"] == "Integrations: MCP servers and tools"

    def test_handle_model_command_shows_current(self):
        agent = SimpleNamespace(
            llm=SimpleNamespace(provider="google", model="old-model"),
            config=SimpleNamespace(llm=SimpleNamespace(provider="google", model="old-model")),
        )
        handled, msg = _handle_model_command(agent, "/model")
        assert handled is True
        assert "google" in msg
        assert "old-model" in msg

    def test_handle_model_list_command_shows_known_models(self):
        handled, msg = _handle_model_list_command("/model-list")
        assert handled is True
        assert "google" in msg
        assert "openai" in msg
        assert "anthropic" in msg

    def test_handle_model_set_command_can_switch_provider_and_model(self, monkeypatch):
        monkeypatch.setattr(
            "archon.cli.LLMClient",
            lambda provider, model, api_key, temperature, base_url: SimpleNamespace(
                provider=provider, model=model
            ),
        )
        agent = SimpleNamespace(
            llm=SimpleNamespace(provider="google", model="gemini-x"),
            config=SimpleNamespace(
                llm=SimpleNamespace(
                    provider="google",
                    model="gemini-x",
                    api_key="test-key",
                    base_url="",
                    fallback_provider="google",
                    fallback_model="gemini-3-flash-preview",
                    fallback_api_key="",
                    fallback_base_url="",
                ),
                agent=SimpleNamespace(temperature=0.3),
            ),
        )
        handled, msg = _handle_model_set_command(agent, "/model-set openai-gpt-5-mini")
        assert handled is True
        assert "openai" in msg
        assert "gpt-5-mini" in msg
        assert agent.llm.provider == "openai"
        assert agent.llm.model == "gpt-5-mini"
        assert agent.config.llm.provider == "openai"
        assert agent.config.llm.model == "gpt-5-mini"

    def test_handle_model_set_command_rejects_spaces(self, monkeypatch):
        monkeypatch.setattr(
            "archon.cli.LLMClient",
            lambda provider, model, api_key, temperature, base_url: SimpleNamespace(
                provider=provider, model=model
            ),
        )
        agent = SimpleNamespace(
            llm=SimpleNamespace(provider="google", model="gemini-x"),
            config=SimpleNamespace(
                llm=SimpleNamespace(
                    provider="google",
                    model="gemini-x",
                    api_key="test-key",
                    base_url="",
                    fallback_provider="openai",
                    fallback_model="gpt-5-mini",
                    fallback_api_key="",
                    fallback_base_url="",
                ),
                agent=SimpleNamespace(temperature=0.3),
            ),
        )
        handled, msg = _handle_model_set_command(agent, "/model-set openai gpt-5-mini")
        assert handled is True
        assert "no spaces" in msg

    def test_set_calls_enabled_in_toml_appends_calls_section(self):
        text = "[llm]\nprovider = \"google\"\n"
        out = _set_calls_enabled_in_toml(text, True)
        assert "[calls]" in out
        assert "enabled = true" in out

    def test_set_calls_enabled_in_toml_updates_existing_calls_section(self):
        text = "[calls]\nenabled = false\n\n[calls.voice_service]\nbase_url = \"http://127.0.0.1:8788\"\n"
        out = _set_calls_enabled_in_toml(text, True)
        assert "[calls]\nenabled = true" in out
        assert "[calls.voice_service]" in out

    def test_handle_repl_command_calls_on_updates_agent_config(self, monkeypatch):
        agent = SimpleNamespace(
            llm=SimpleNamespace(model="gemini-x"),
            config=SimpleNamespace(
                llm=SimpleNamespace(model="gemini-x"),
                calls=SimpleNamespace(enabled=False, voice_service=SimpleNamespace(base_url="http://127.0.0.1:8788")),
            ),
        )
        seen = {}

        def fake_set(enabled):
            seen["enabled"] = enabled
            return "/tmp/config.toml"

        monkeypatch.setattr("archon.cli._set_calls_enabled_config", fake_set)

        action, msg = _handle_repl_command(agent, "/calls on")
        assert action == "calls"
        assert "enabled" in msg.lower()
        assert seen["enabled"] is True
        assert agent.config.calls.enabled is True

    def test_bare_slash_shows_command_list(self):
        agent = SimpleNamespace(
            llm=SimpleNamespace(model="test"),
            config=SimpleNamespace(llm=SimpleNamespace(model="test")),
        )
        action, msg = _handle_repl_command(agent, "/")
        assert action == "help"
        assert "Available commands:" in msg
        assert "Shell: current status" in msg
        assert "Shell: skills" in msg
        assert "Shell: plugins" in msg
        assert "Model: current provider/model" in msg
        for name, _desc in _SLASH_COMMANDS:
            assert name in msg

    def test_handle_repl_command_call_alias_on_updates_agent_config(self, monkeypatch):
        agent = SimpleNamespace(
            llm=SimpleNamespace(model="gemini-x"),
            config=SimpleNamespace(
                llm=SimpleNamespace(model="gemini-x"),
                calls=SimpleNamespace(enabled=False, voice_service=SimpleNamespace(base_url="http://127.0.0.1:8788")),
            ),
        )
        seen = {}

        def fake_set(enabled):
            seen["enabled"] = enabled
            return "/tmp/config.toml"

        monkeypatch.setattr("archon.cli._set_calls_enabled_config", fake_set)

        action, msg = _handle_repl_command(agent, "/call on")
        assert action == "calls"
        assert "enabled" in msg.lower()
        assert seen["enabled"] is True
        assert agent.config.calls.enabled is True

    def test_handle_repl_command_profile_show_reports_active_and_available(self):
        agent = SimpleNamespace(
            llm=SimpleNamespace(model="gemini-x"),
            policy_profile="safe",
            config=SimpleNamespace(
                llm=SimpleNamespace(model="gemini-x"),
                profiles={"default": object(), "safe": object()},
            ),
        )

        action, msg = _handle_repl_command(agent, "/profile show")

        assert action == "profile"
        assert "Policy profile: safe" in msg
        assert "default" in msg
        assert "safe" in msg

    def test_handle_repl_command_profile_set_updates_agent_profile(self):
        class _FakeAgent:
            def __init__(self):
                self.policy_profile = "default"
                self.llm = SimpleNamespace(model="gemini-x")
                self.config = SimpleNamespace(
                    llm=SimpleNamespace(model="gemini-x"),
                    profiles={"default": object(), "safe": object()},
                )

            def set_policy_profile(self, profile):
                self.policy_profile = profile

        agent = _FakeAgent()

        action, msg = _handle_repl_command(agent, "/profile set safe")

        assert action == "profile"
        assert "Policy profile set to: safe" in msg
        assert agent.policy_profile == "safe"

    def test_handle_repl_command_profile_set_rejects_unknown_profile(self):
        agent = SimpleNamespace(
            llm=SimpleNamespace(model="gemini-x"),
            policy_profile="default",
            config=SimpleNamespace(
                llm=SimpleNamespace(model="gemini-x"),
                profiles={"default": object()},
            ),
        )

        action, msg = _handle_repl_command(agent, "/profile set safe")

        assert action == "profile"
        assert "Unknown profile 'safe'" in msg
        assert agent.policy_profile == "default"

    def test_handle_repl_command_jobs_lists_recent_jobs(self, monkeypatch):
        agent = SimpleNamespace(
            llm=SimpleNamespace(model="test"),
            config=SimpleNamespace(llm=SimpleNamespace(model="test")),
        )
        jobs = [
            SimpleNamespace(
                job_id="worker:sess-1",
                kind="worker_session",
                status="ok",
                summary="Looks good",
                last_update_at="2026-02-24T00:00:10Z",
            ),
            SimpleNamespace(
                job_id="call:call-1",
                kind="call_mission",
                status="queued",
                summary="Call me",
                last_update_at="2026-02-24T00:00:09Z",
            ),
        ]
        monkeypatch.setattr("archon.cli_repl_commands._collect_job_summaries", lambda limit=10: jobs)

        action, msg = _handle_repl_command(agent, "/jobs")

        assert action == "jobs"
        assert "worker:sess-1" in msg
        assert "call:call-1" in msg

    def test_handle_repl_command_job_shows_summary(self, monkeypatch):
        agent = SimpleNamespace(
            llm=SimpleNamespace(model="test"),
            config=SimpleNamespace(llm=SimpleNamespace(model="test")),
        )
        job = SimpleNamespace(
            job_id="worker:sess-1",
            kind="worker_session",
            status="ok",
            summary="Looks good",
            last_update_at="2026-02-24T00:00:10Z",
        )
        monkeypatch.setattr(
            "archon.cli_repl_commands._load_job_summary",
            lambda job_ref: job if job_ref == "worker:sess-1" else None,
        )

        action, msg = _handle_repl_command(agent, "/job worker:sess-1")

        assert action == "job"
        assert "job_id: worker:sess-1" in msg
        assert "job_kind: worker_session" in msg
        assert "job_status: ok" in msg
        assert "job_summary: Looks good" in msg

    def test_handle_repl_command_mcp_servers_lists_configured_servers(self):
        cfg = Config()
        cfg.mcp.servers = {
            "docs": MCPServerConfig(
                enabled=True,
                mode="read_only",
                transport="stdio",
                command=["python", "server.py"],
            )
        }
        agent = SimpleNamespace(
            llm=SimpleNamespace(model="test"),
            config=cfg,
        )

        action, msg = _handle_repl_command(agent, "/mcp servers")

        assert action == "mcp"
        assert "docs" in msg
        assert "read_only" in msg
        assert "stdio" in msg

    def test_handle_repl_command_mcp_tools_lists_server_tools(self, monkeypatch):
        cfg = Config()
        cfg.mcp.servers = {
            "docs": MCPServerConfig(
                enabled=True,
                mode="read_only",
                transport="stdio",
                command=["python", "server.py"],
            )
        }
        agent = SimpleNamespace(
            llm=SimpleNamespace(model="test"),
            config=cfg,
        )

        class _FakeClient:
            def __init__(self, config):
                self.config = config

            def list_tools(self, server_name, transport_fn=None):
                assert transport_fn is None
                assert server_name == "docs"
                return {
                    "server": "docs",
                    "tools": [
                        {"name": "search_docs", "description": "Search the docs"},
                    ],
                }

        monkeypatch.setattr("archon.cli_repl_commands.MCPClient", _FakeClient)

        action, msg = _handle_repl_command(agent, "/mcp tools docs")

        assert action == "mcp"
        assert "search_docs" in msg
        assert "Search the docs" in msg

    def test_handle_repl_command_status_reports_compact_local_summary(self):
        agent = self._make_local_command_agent()

        action, msg = _handle_repl_command(agent, "/status")

        assert action == "status"
        assert msg == "Status: model=openai/gpt-5-mini | profile=safe | calls=on | mcp=1/2 | tokens=150"

    def test_handle_repl_command_cost_reports_session_token_totals(self):
        agent = self._make_local_command_agent()

        action, msg = _handle_repl_command(agent, "/cost")

        assert action == "cost"
        assert msg == "Cost: total_tokens=150 | input=120 | output=30"

    def test_handle_repl_command_doctor_reports_compact_health_summary(self):
        agent = self._make_local_command_agent()

        action, msg = _handle_repl_command(agent, "/doctor")

        assert action == "doctor"
        assert msg == "Doctor: llm=ok | profile=ok | calls=on | mcp=1/2"

    def test_handle_repl_command_permissions_reports_active_profile_permissions(self):
        agent = self._make_local_command_agent()

        action, msg = _handle_repl_command(agent, "/permissions")

        assert action == "permissions"
        assert msg == "Permissions: profile=safe | mode=review | tools=2 [read_file,shell]"

    def test_handle_repl_command_status_reports_compact_shell_state(self):
        cfg = Config()
        cfg.orchestrator.enabled = True
        cfg.orchestrator.mode = "hybrid"
        cfg.orchestrator.shadow_eval = False
        cfg.calls.enabled = True
        cfg.mcp.servers = {
            "exa": MCPServerConfig(
                enabled=True,
                mode="read_only",
                transport="stdio",
                command=["node", "exa.js"],
            )
        }
        agent = SimpleNamespace(
            llm=SimpleNamespace(provider="google", model="gemini-x"),
            total_input_tokens=120,
            total_output_tokens=30,
            policy_profile="safe",
            config=cfg,
        )

        action, msg = _handle_repl_command(agent, "/status")

        assert action == "status"
        assert "Status:" in msg
        assert "model=google/gemini-x" in msg
        assert "profile=safe" in msg
        assert "orchestrator=hybrid" in msg
        assert "calls=on" in msg
        assert "mcp=1/1" in msg
        assert "tokens=150" in msg

    def test_handle_repl_command_cost_reports_session_token_usage(self):
        agent = SimpleNamespace(
            llm=SimpleNamespace(provider="google", model="gemini-x"),
            total_input_tokens=47000,
            total_output_tokens=921,
            config=Config(),
        )

        action, msg = _handle_repl_command(agent, "/cost")

        assert action == "cost"
        assert "Cost:" in msg
        assert "total_tokens=47,921" in msg
        assert "input=47,000" in msg
        assert "output=921" in msg

    def test_handle_repl_command_doctor_reports_ready_when_core_runtime_is_configured(self):
        cfg = Config()
        cfg.llm.provider = "google"
        cfg.llm.api_key = "test-key"
        cfg.calls.enabled = False
        cfg.telegram.enabled = False
        cfg.mcp.servers = {
            "exa": MCPServerConfig(
                enabled=True,
                mode="read_only",
                transport="stdio",
                command=["node", "exa.js"],
            )
        }
        agent = SimpleNamespace(
            llm=SimpleNamespace(provider="google", model="gemini-x"),
            config=cfg,
        )

        action, msg = _handle_repl_command(agent, "/doctor")

        assert action == "doctor"
        assert "Doctor:" in msg
        assert "llm=ok" in msg
        assert "profile=ok" in msg
        assert "calls=off" in msg
        assert "mcp=1/1" in msg

    def test_handle_repl_command_doctor_reports_missing_llm_credentials(self):
        cfg = Config()
        cfg.llm.provider = "google"
        cfg.llm.model = "gemini-x"
        cfg.llm.api_key = ""
        agent = SimpleNamespace(
            llm=SimpleNamespace(provider="google", model="gemini-x"),
            config=cfg,
        )

        action, msg = _handle_repl_command(agent, "/doctor")

        assert action == "doctor"
        assert "llm=missing" in msg
        assert "profile=ok" in msg

    def test_handle_repl_command_permissions_reports_active_policy(self):
        cfg = Config()
        cfg.safety.default_action = "confirm"
        cfg.orchestrator.enabled = True
        cfg.orchestrator.mode = "hybrid"
        cfg.orchestrator.shadow_eval = False
        cfg.profiles = {
            "default": cfg.profiles["default"],
            "safe": type(cfg.profiles["default"])(
                allowed_tools=["memory_read"],
                max_mode="review",
                execution_backend="host",
            ),
        }
        agent = SimpleNamespace(
            llm=SimpleNamespace(provider="google", model="gemini-x"),
            policy_profile="safe",
            config=cfg,
        )

        action, msg = _handle_repl_command(agent, "/permissions")

        assert action == "permissions"
        assert "Permissions:" in msg
        assert "profile=safe" in msg
        assert "mode=review" in msg
        assert "tools=1 [memory_read]" in msg

    def test_handle_repl_command_invalid_profile_is_reported_consistently(self):
        cfg = Config()
        cfg.llm.api_key = "test-key"
        agent = SimpleNamespace(
            llm=SimpleNamespace(provider="google", model="gemini-x"),
            policy_profile="missing-profile",
            config=cfg,
        )

        doctor_action, doctor_msg = _handle_repl_command(agent, "/doctor")
        permissions_action, permissions_msg = _handle_repl_command(agent, "/permissions")

        assert doctor_action == "doctor"
        assert "profile=missing-profile->default" in doctor_msg
        assert permissions_action == "permissions"
        assert "profile=missing-profile->default" in permissions_msg

    def test_handle_repl_command_skills_lists_available_and_active_skill(self):
        agent = self._make_local_command_agent()

        action, msg = _handle_repl_command(agent, "/skills")

        assert action == "skills"
        assert msg == (
            "Skills: active=none | available=general, coder, researcher, operator, sales, memory_curator"
        )

    def test_handle_repl_command_skills_show_reports_skill_details(self):
        agent = self._make_local_command_agent()

        action, msg = _handle_repl_command(agent, "/skills show coder")

        assert action == "skills"
        assert msg == (
            "Skill coder: mode=implement | provider=anthropic | model=claude-sonnet-4-6 | tools=20"
        )

    def test_handle_repl_command_skills_use_sets_session_skill_profile(self):
        agent = self._make_local_command_agent()

        action, msg = _handle_repl_command(agent, "/skills use coder")

        assert action == "skills"
        assert msg == "Skill set to: coder"
        assert agent.policy_profile != "safe"
        assert agent.policy_profile.startswith("__skill__:")
        assert _build_skill_guidance(agent.config, profile_name=agent.policy_profile)
        assert "Active skill: coder" in _build_skill_guidance(
            agent.config, profile_name=agent.policy_profile
        )

        list_action, list_msg = _handle_repl_command(agent, "/skills")
        assert list_action == "skills"
        assert list_msg == (
            "Skills: active=coder | available=general, coder, researcher, operator, sales, memory_curator"
        )

        profile_action, profile_msg = _handle_repl_command(agent, "/profile show")
        assert profile_action == "profile"
        assert profile_msg == "Policy profile: safe | skill: coder | available: default, safe"

        permissions_action, permissions_msg = _handle_repl_command(agent, "/permissions")
        assert permissions_action == "permissions"
        assert permissions_msg == (
            "Permissions: profile=safe | skill=coder | mode=review | tools=2 [read_file,shell]"
        )

    def test_handle_repl_command_skills_clear_restores_base_profile(self):
        agent = self._make_local_command_agent()
        _handle_repl_command(agent, "/skills use coder")

        action, msg = _handle_repl_command(agent, "/skills clear")

        assert action == "skills"
        assert msg == "Skill cleared"
        assert agent.policy_profile == "safe"

    def test_handle_repl_command_plugins_lists_native_and_mcp_plugins(self):
        agent = self._make_local_command_agent()

        action, msg = _handle_repl_command(agent, "/plugins")

        assert action == "plugins"
        assert msg == (
            "Plugins: enabled=calls, web, mcp:docs | available=calls, telegram, web, mcp:docs, mcp:build"
        )

    def test_handle_repl_command_plugins_show_reports_plugin_details(self):
        agent = self._make_local_command_agent()

        action, msg = _handle_repl_command(agent, "/plugins show mcp:docs")

        assert action == "plugins"
        assert msg == "Plugin mcp:docs: type=mcp | enabled=on | mode=read_only | transport=stdio"

    def test_chat_cmd_handles_status_locally_without_model_turn(self):
        outputs = []

        class _FailIfRunAgent:
            def __init__(self):
                self.hooks = HookBus()
                self.config = Config()
                self.config.llm.model = "test-model"
                self.total_input_tokens = 0
                self.total_output_tokens = 0
                self.log_label = ""
                self.policy_profile = "default"
                self.on_thinking = None
                self.on_tool_call = None

            def run(self, _text):
                raise AssertionError("agent.run should not be called for /status")

            def reset(self):
                return None

        agent = _FailIfRunAgent()
        inputs = iter(["/status", "quit"])
        session_ids = iter(["sess-1", "sess-2"])

        _chat_cmd(
            make_agent_fn=lambda: agent,
            make_telegram_adapter_fn=lambda _cfg: None,
            new_session_id_fn=lambda: next(session_ids),
            save_exchange_fn=lambda *_args: None,
            slash_completer_fn=lambda *_args: None,
            pick_slash_command_fn=lambda: None,
            is_bracketed_paste_start_fn=lambda _text: False,
            collect_bracketed_paste_fn=lambda *_args, **_kwargs: "",
            is_paste_command_fn=lambda _text: False,
            collect_paste_message_fn=lambda *_args, **_kwargs: "",
            handle_repl_command_fn=_handle_repl_command,
            is_model_runtime_error_fn=lambda _err: False,
            format_session_summary_fn=_format_session_summary,
            format_chat_response_fn=lambda text: text,
            format_turn_stats_fn=_format_turn_stats,
            make_readline_prompt_fn=lambda label, _ansi: label,
            spinner_cls=_FakeSpinner,
            ansi_prompt_user="",
            ansi_error="",
            ansi_reset="",
            click_echo_fn=lambda text="", err=False: outputs.append((text, err)),
            input_fn=lambda _prompt: next(inputs),
            readline_module=_FakeReadline(),
            time_time_fn=lambda: 0.0,
            version="test",
        )

        plain = [re.sub(r"\x1b\[[0-9;]*m", "", text) for text, _err in outputs]
        assert any(text.startswith("Status:") for text in plain)


class _FakeReadline:
    def set_completer(self, _fn):
        return None

    def set_completer_delims(self, _value):
        return None

    def parse_and_bind(self, _value):
        return None


class _FakeSpinner:
    def start(self, _label="thinking"):
        return None

    def stop(self):
        return None


class _RouteEventAgent:
    def __init__(self, events_by_message):
        self.events_by_message = events_by_message
        self.hooks = HookBus()
        self.config = SimpleNamespace(
            telegram=SimpleNamespace(
                enabled=False,
                connect_on_chat=False,
                allowed_user_ids=[],
                poll_timeout_sec=30,
            ),
            llm=SimpleNamespace(model="test-model"),
        )
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.log_label = ""
        self._turn_no = 0
        self.on_thinking = None
        self.on_tool_call = None

    def run(self, text):
        self._turn_no += 1
        turn_id = f"t{self._turn_no:03d}"
        for lane, reason in self.events_by_message.get(text, []):
            self.hooks.emit_kind(
                "orchestrator.route",
                task_id=turn_id,
                payload={
                    "turn_id": turn_id,
                    "lane": lane,
                    "reason": reason,
                },
            )
        self.total_input_tokens += 10
        self.total_output_tokens += 2
        return f"ok:{text}"

    def reset(self):
        return None


class _LocalCommandAgent:
    def __init__(self):
        cfg = Config()
        cfg.llm.provider = "openai"
        cfg.llm.model = "gpt-5-mini"
        cfg.llm.api_key = "test-key"
        cfg.calls.enabled = True
        cfg.mcp.servers = {
            "docs": MCPServerConfig(enabled=True, mode="read_only", transport="stdio"),
            "build": MCPServerConfig(enabled=False, mode="read_write", transport="stdio"),
        }
        cfg.profiles = {
            "default": ProfileConfig(),
            "safe": ProfileConfig(allowed_tools=["shell", "read_file"], max_mode="review"),
        }
        self.hooks = HookBus()
        self.config = cfg
        self.llm = SimpleNamespace(provider="openai", model="gpt-5-mini")
        self.policy_profile = "safe"
        self.total_input_tokens = 120
        self.total_output_tokens = 30
        self.history = []
        self.log_label = ""
        self.run_calls = []
        self.on_thinking = None
        self.on_tool_call = None

    def run(self, text):
        self.run_calls.append(text)
        raise AssertionError(f"agent.run should not be called for local command: {text}")

    def reset(self):
        return None


def _run_chat_session(agent, inputs):
    outputs = []
    input_iter = iter(inputs)
    session_ids = iter(["sess-1", "sess-2", "sess-3"])
    tick = {"value": 0}

    def fake_input(_prompt):
        value = next(input_iter)
        if isinstance(value, BaseException):
            raise value
        return value

    def fake_handle_repl_command(_agent, text):
        if text == "/reset":
            return "reset", ""
        return None, ""

    def fake_time():
        tick["value"] += 1
        return float(tick["value"])

    _chat_cmd(
        make_agent_fn=lambda: agent,
        make_telegram_adapter_fn=lambda _cfg: None,
        new_session_id_fn=lambda: next(session_ids),
        save_exchange_fn=lambda *_args: None,
        slash_completer_fn=lambda *_args: None,
        pick_slash_command_fn=lambda: None,
        is_bracketed_paste_start_fn=lambda _text: False,
        collect_bracketed_paste_fn=lambda *_args, **_kwargs: "",
        is_paste_command_fn=lambda _text: False,
        collect_paste_message_fn=lambda *_args, **_kwargs: "",
        handle_repl_command_fn=fake_handle_repl_command,
        is_model_runtime_error_fn=lambda _err: False,
        format_session_summary_fn=_format_session_summary,
        format_chat_response_fn=lambda text: text,
        format_turn_stats_fn=_format_turn_stats,
        make_readline_prompt_fn=lambda label, _ansi: label,
        spinner_cls=_FakeSpinner,
        ansi_prompt_user="",
        ansi_error="",
        ansi_reset="",
        click_echo_fn=lambda text="", err=False: outputs.append((text, err)),
        input_fn=fake_input,
        readline_module=_FakeReadline(),
        time_time_fn=fake_time,
        version="test",
    )
    return outputs


def _run_local_command_session(agent, inputs):
    outputs = []
    input_iter = iter(inputs)
    session_ids = iter(["sess-1", "sess-2"])

    def fake_input(_prompt):
        value = next(input_iter)
        if isinstance(value, BaseException):
            raise value
        return value

    _chat_cmd(
        make_agent_fn=lambda: agent,
        make_telegram_adapter_fn=lambda _cfg: None,
        new_session_id_fn=lambda: next(session_ids),
        save_exchange_fn=lambda *_args: None,
        slash_completer_fn=lambda *_args: None,
        pick_slash_command_fn=lambda: None,
        is_bracketed_paste_start_fn=lambda _text: False,
        collect_bracketed_paste_fn=lambda *_args, **_kwargs: "",
        is_paste_command_fn=lambda _text: False,
        collect_paste_message_fn=lambda *_args, **_kwargs: "",
        handle_repl_command_fn=_handle_repl_command,
        is_model_runtime_error_fn=lambda _err: False,
        format_session_summary_fn=_format_session_summary,
        format_chat_response_fn=lambda text: text,
        format_turn_stats_fn=_format_turn_stats,
        make_readline_prompt_fn=lambda label, _ansi: label,
        spinner_cls=_FakeSpinner,
        ansi_prompt_user="",
        ansi_error="",
        ansi_reset="",
        click_echo_fn=lambda text="", err=False: outputs.append((text, err)),
        input_fn=fake_input,
        readline_module=_FakeReadline(),
        time_time_fn=lambda: 1.0,
        version="test",
    )
    return outputs


class TestCliRouteState:
    @staticmethod
    def _plain_outputs(outputs):
        return [re.sub(r"\x1b\[[0-9;]*m", "", text) for text, _err in outputs]

    def test_chat_session_counts_route_once_per_turn(self):
        agent = _RouteEventAgent(
            {
                "hello": [
                    ("job", "broad_scope_request"),
                    ("job", "broad_scope_request"),
                ]
            }
        )

        outputs = self._plain_outputs(_run_chat_session(agent, ["hello", "quit"]))
        summary = next(text for text in outputs if text.startswith("Session:") and "turns" in text)

        assert "routes:" in summary
        assert "job=1" in summary
        assert "job=2" not in summary

    def test_chat_reset_clears_route_counts_for_new_session(self):
        agent = _RouteEventAgent(
            {
                "hello": [("fast", "simple_chat")],
                "again": [("job", "broad_scope_request")],
            }
        )

        outputs = self._plain_outputs(_run_chat_session(agent, ["hello", "/reset", "again", "quit"]))
        summary = next(text for text in outputs if text.startswith("Session:") and "turns" in text)

        assert "job=1" in summary
        assert "fast=1" not in summary


class TestCliLocalInteractiveCommands:
    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            ("/status", "Status: model=openai/gpt-5-mini | profile=safe | calls=on | mcp=1/2 | tokens=150"),
            ("/cost", "Cost: total_tokens=150 | input=120 | output=30"),
            ("/doctor", "Doctor: llm=ok | profile=ok | calls=on | mcp=1/2"),
            ("/permissions", "Permissions: profile=safe | mode=review | tools=2 [read_file,shell]"),
        ],
    )
    def test_local_shell_commands_do_not_call_agent_run(self, command, expected):
        agent = _LocalCommandAgent()

        outputs = [re.sub(r"\x1b\[[0-9;]*m", "", text) for text, _err in _run_local_command_session(agent, [command, "quit"])]

        assert expected in outputs
        assert agent.run_calls == []

    def test_local_skills_commands_do_not_call_agent_run(self):
        agent = _LocalCommandAgent()

        outputs = [
            re.sub(r"\x1b\[[0-9;]*m", "", text)
            for text, _err in _run_local_command_session(
                agent,
                ["/skills use coder", "/skills clear", "quit"],
            )
        ]

        assert "Skill set to: coder" in outputs
        assert "Skill cleared" in outputs
        assert agent.run_calls == []

    def test_local_plugins_commands_do_not_call_agent_run(self):
        agent = _LocalCommandAgent()

        outputs = [
            re.sub(r"\x1b\[[0-9;]*m", "", text)
            for text, _err in _run_local_command_session(
                agent,
                ["/plugins", "/plugins show mcp:docs", "quit"],
            )
        ]

        assert "Plugins: enabled=calls, web, mcp:docs | available=calls, telegram, web, mcp:docs, mcp:build" in outputs
        assert "Plugin mcp:docs: type=mcp | enabled=on | mode=read_only | transport=stdio" in outputs
        assert agent.run_calls == []

    def test_local_control_commands_do_not_mutate_agent_history(self):
        agent = _LocalCommandAgent()

        _run_local_command_session(agent, ["/skills", "/plugins", "quit"])

        assert agent.history == []


class TestSlashCompleter:
    def test_matches_prefix(self):
        assert _slash_completer("/mo", 0) == "/model"
        assert _slash_completer("/mo", 1) == "/model-list"
        assert _slash_completer("/mo", 2) == "/model-set"
        assert _slash_completer("/mo", 3) is None

    def test_job_prefix_matches_job_commands(self):
        assert _slash_completer("/jo", 0) == "/jobs"
        assert _slash_completer("/jo", 1) == "/job"
        assert _slash_completer("/jo", 2) is None

    def test_mcp_prefix_matches_command(self):
        assert _slash_completer("/mc", 0) == "/mcp"
        assert _slash_completer("/mc", 1) is None

    def test_skills_prefix_matches_command(self):
        assert _slash_completer("/sk", 0) == "/skills"
        assert _slash_completer("/sk", 1) is None

    def test_plugins_prefix_matches_command(self):
        assert _slash_completer("/pl", 0) == "/plugins"
        assert _slash_completer("/pl", 1) is None

    def test_empty_returns_all(self):
        results = []
        for i in range(len(_SLASH_COMMANDS) + 1):
            val = _slash_completer("", i)
            if val is None:
                break
            results.append(val)
        assert len(results) == len(_SLASH_COMMANDS)

    def test_no_match(self):
        assert _slash_completer("/xyz", 0) is None

    def test_slash_alone_matches_all(self):
        results = []
        for i in range(len(_SLASH_COMMANDS) + 1):
            val = _slash_completer("/", i)
            if val is None:
                break
            results.append(val)
        assert len(results) == len(_SLASH_COMMANDS)

    def test_non_slash_text_returns_none(self):
        assert _slash_completer("model", 0) is None

    def test_profile_subcommand_completion_from_line_buffer(self, monkeypatch):
        monkeypatch.setattr("archon.cli.readline.get_line_buffer", lambda: "/profile ")
        assert _slash_completer("", 0) == "show"
        assert _slash_completer("", 1) == "set"
        assert _slash_completer("", 2) is None

    def test_calls_subcommand_completion_from_line_buffer_prefix(self, monkeypatch):
        monkeypatch.setattr("archon.cli.readline.get_line_buffer", lambda: "/calls o")
        assert _slash_completer("o", 0) == "on"
        assert _slash_completer("o", 1) == "off"
        assert _slash_completer("o", 2) is None

    def test_skills_subcommand_completion_from_line_buffer(self, monkeypatch):
        monkeypatch.setattr("archon.cli.readline.get_line_buffer", lambda: "/skills ")
        assert _slash_completer("", 0) == "list"
        assert _slash_completer("", 1) == "show"
        assert _slash_completer("", 2) == "use"
        assert _slash_completer("", 3) == "clear"
        assert _slash_completer("", 4) is None

    def test_plugins_subcommand_completion_from_line_buffer(self, monkeypatch):
        monkeypatch.setattr("archon.cli.readline.get_line_buffer", lambda: "/plugins ")
        assert _slash_completer("", 0) == "list"
        assert _slash_completer("", 1) == "show"
        assert _slash_completer("", 2) is None


class TestCliPhaseLabels:
    def test_tool_spinner_label_distinguishes_mcp_and_worker_tools(self):
        assert _tool_spinner_label("mcp_call", {"server": "exa", "tool": "web_search"}) == "mcp exa"
        assert _tool_spinner_label("worker_send", {"session_id": "sess-12345678"}) == "worker send sess-123"


class TestPickSlashCommand:
    def test_run_picker_returns_none_for_empty_items(self):
        assert _run_picker([], label_width=5) is None

    def test_run_picker_returns_none_when_not_tty(self, monkeypatch):
        monkeypatch.setattr("archon.cli.sys.stdin", SimpleNamespace(fileno=lambda: 0))
        monkeypatch.setattr("archon.cli.os.isatty", lambda _fd: False)
        assert _run_picker([("a", "b")], label_width=5) is None

    def test_returns_none_when_not_a_tty(self, monkeypatch):
        monkeypatch.setattr("os.isatty", lambda _fd: False)
        assert _pick_slash_command() is None

    def test_build_model_set_subvalues(self):
        values = _build_model_set_subvalues()
        assert values
        names = {name for name, _ in values}
        assert "google-gemini-3.1-pro-preview" in names
        assert "openai-gpt-5.2" in names
        assert "anthropic-claude-sonnet-4-20250514" in names

    def test_slash_subvalues_map(self):
        assert "/model-set" in _SLASH_SUBVALUES
        assert "/calls" in _SLASH_SUBVALUES
        assert "/profile" in _SLASH_SUBVALUES
        assert "/skills" in _SLASH_SUBVALUES
        assert "/plugins" in _SLASH_SUBVALUES
        call_values = [value for value, _desc in _SLASH_SUBVALUES["/calls"]]
        assert call_values == ["status", "on", "off"]
        profile_values = [value for value, _desc in _SLASH_SUBVALUES["/profile"]]
        assert profile_values == ["show", "set default"]
        skill_values = [value for value, _desc in _SLASH_SUBVALUES["/skills"]]
        assert skill_values == ["list", "show coder", "use coder", "clear"]
        plugin_values = [value for value, _desc in _SLASH_SUBVALUES["/plugins"]]
        assert plugin_values == ["list", "show calls", "show mcp:docs"]

    def test_pick_slash_command_two_level(self, monkeypatch):
        picks = iter(["/calls", "on"])
        monkeypatch.setattr("archon.cli._run_picker", lambda *_a, **_k: next(picks))
        assert _pick_slash_command() == "/calls on"
