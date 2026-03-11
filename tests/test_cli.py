"""CLI formatting helper tests."""

import io
import re
from contextlib import redirect_stderr
from types import SimpleNamespace

import pytest

from archon.config import Config, MCPServerConfig, ProfileConfig
from archon.cli import _format_chat_response
from archon.cli import _is_paste_command, _collect_paste_message
from archon.cli import (
    _make_readline_prompt,
    _make_runtime_prompt,
    _build_model_set_subvalues,
    _build_slash_subvalues,
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
from archon.cli_interactive_commands import telegram_cmd as _telegram_cmd
from archon.cli_interactive_commands import _tool_spinner_label
from archon.cli_repl_commands import _maybe_auto_activate_skill
from archon.control.hooks import HookBus
from archon.prompt import build_skill_guidance as _build_skill_guidance
from archon.safety import Level
from archon.ux.events import ActivityEvent


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

    def test_telegram_cmd_reports_telegram_mode_without_phase_label(self):
        outputs = []
        cfg = Config()
        cfg.telegram.allowed_user_ids = [1, 2]

        class _Adapter:
            def run_forever(self):
                return None

        _telegram_cmd(
            load_config_fn=lambda: cfg,
            ensure_dirs_fn=lambda: None,
            make_telegram_adapter_fn=lambda _cfg: _Adapter(),
            click_echo_fn=lambda text="", err=False: outputs.append((text, err)),
            exit_fn=lambda code: (_ for _ in ()).throw(AssertionError(f"unexpected exit {code}")),
            version="test",
        )

        plain = [re.sub(r"\x1b\[[0-9;]*m", "", text) for text, _err in outputs]
        assert "Archon vtest | Telegram adapter running" in plain
        assert "Allowed users: 2" in plain
        assert "Dangerous tool actions are blocked in Telegram mode." in plain

    def test_make_readline_prompt_wraps_non_printing_ansi_sequences(self):
        prompt = _make_readline_prompt("you>", "\033[93;1m")
        assert prompt == "\x01\033[93;1m\x02you>\x01\033[0m\x02 "

    def test_make_runtime_prompt_uses_plain_prompt_when_stdio_is_not_tty(self, monkeypatch):
        monkeypatch.setattr("archon.cli.os.isatty", lambda _fd: False)
        monkeypatch.setattr("archon.cli.sys.stdin", SimpleNamespace(fileno=lambda: 0))
        monkeypatch.setattr("archon.cli.sys.stdout", SimpleNamespace(fileno=lambda: 1))

        prompt = _make_runtime_prompt("you>", "\033[93;1m")

        assert prompt == "you> "

    def test_make_runtime_prompt_uses_readline_prompt_when_stdio_is_tty(self, monkeypatch):
        monkeypatch.setattr("archon.cli.os.isatty", lambda _fd: True)
        monkeypatch.setattr("archon.cli.sys.stdin", SimpleNamespace(fileno=lambda: 0))
        monkeypatch.setattr("archon.cli.sys.stdout", SimpleNamespace(fileno=lambda: 1))

        prompt = _make_runtime_prompt("you>", "\033[93;1m")

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

    def test_format_turn_stats_includes_route_path_description_when_present(self):
        out = self._plain(
            _format_turn_stats(
                1.23,
                1234,
                56,
                1234,
                56,
                route_lane="operator",
                route_reason="native_research_status_request",
                route_path="hybrid_shared_executor",
            )
        )
        assert "route: operator" in out
        assert "shared-executor-routing" in out

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
            session_id="sess-usage",
        )

    def test_slash_commands_include_local_shell_status_commands(self):
        names = {name for name, _desc in _SLASH_COMMANDS}
        assert {"/status", "/cost", "/doctor", "/permissions"} <= names
        assert "/model" in names
        assert "/model-list" not in names
        assert "/model-set" not in names

    def test_slash_commands_include_terminal_approval_commands(self):
        names = {name for name, _desc in _SLASH_COMMANDS}
        assert {"/approvals", "/approve", "/deny", "/approve_next"} <= names

    def test_slash_command_descriptions_group_shell_controls(self):
        descriptions = dict(_SLASH_COMMANDS)
        assert descriptions["/status"] == "Shell: current status"
        assert descriptions["/skills"] == "Shell: skills"
        assert descriptions["/plugins"] == "Shell: plugins"
        assert descriptions["/model"] == "Model: current or set provider/model"
        assert descriptions["/mcp"] == "Integrations: MCP servers and tools"

    def test_slash_command_descriptions_include_terminal_approval_controls(self):
        descriptions = dict(_SLASH_COMMANDS)
        assert descriptions["/approvals"] == "Shell: approvals status/on/off"
        assert descriptions["/approve"] == "Shell: approve pending request"
        assert descriptions["/deny"] == "Shell: deny pending request"
        assert descriptions["/approve_next"] == "Shell: approve next dangerous action"

    def test_handle_model_command_shows_current(self):
        agent = SimpleNamespace(
            llm=SimpleNamespace(provider="google", model="old-model"),
            config=SimpleNamespace(llm=SimpleNamespace(provider="google", model="old-model")),
        )
        handled, msg = _handle_model_command(agent, "/model")
        assert handled is True
        assert "google" in msg
        assert "old-model" in msg

    def test_handle_model_command_show_alias_shows_current(self):
        agent = SimpleNamespace(
            llm=SimpleNamespace(provider="google", model="old-model"),
            config=SimpleNamespace(llm=SimpleNamespace(provider="google", model="old-model")),
        )
        handled, msg = _handle_model_command(agent, "/model show")
        assert handled is True
        assert "google" in msg
        assert "old-model" in msg

    def test_handle_model_command_set_alias_can_switch_provider_and_model(self, monkeypatch):
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

        handled, msg = _handle_model_command(agent, "/model set openai-gpt-5.2")

        assert handled is True
        assert msg == "Model set to: openai/gpt-5.2"
        assert agent.llm.provider == "openai"
        assert agent.llm.model == "gpt-5.2"

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

    def test_handle_repl_command_approvals_reports_default_state(self):
        action, msg = _handle_repl_command(SimpleNamespace(), "/approvals")

        assert action == "approvals"
        assert msg == "Approvals: dangerous_mode=off | pending=none | approve_next_tokens=0"

    def test_handle_repl_command_approvals_status_alias_reports_default_state(self):
        action, msg = _handle_repl_command(SimpleNamespace(), "/approvals status")

        assert action == "approvals"
        assert msg == "Approvals: dangerous_mode=off | pending=none | approve_next_tokens=0"

    def test_handle_repl_command_help_lists_approvals_status_alias(self):
        action, msg = _handle_repl_command(SimpleNamespace(), "/help")

        assert action == "help"
        assert "Core:" in msg
        assert "/status" in msg
        assert "/approvals" in msg
        assert "/jobs" in msg
        assert "Advanced:" in msg
        assert "/cost" in msg
        assert "/permissions" in msg
        assert "/jobs show <job-id>" in msg
        assert "/job <id>" not in msg
        assert "Use / to browse commands." in msg

    def test_handle_repl_command_approvals_toggle_reports_requested_mode_without_state(self):
        action, msg = _handle_repl_command(SimpleNamespace(), "/approvals on")

        assert action == "approvals"
        assert msg == "Approvals: requested=on | state=unavailable"

        action, msg = _handle_repl_command(SimpleNamespace(), "/approvals off")

        assert action == "approvals"
        assert msg == "Approvals: requested=off | state=unavailable"

    @pytest.mark.parametrize("command", ["/approvals foo", "/approvals on extra"])
    def test_handle_repl_command_approvals_rejects_invalid_forms(self, command):
        action, msg = _handle_repl_command(SimpleNamespace(), command)

        assert action == "approvals"
        assert msg == "Usage: /approvals [status|on|off]"

    def test_handle_repl_command_deny_and_approve_report_without_pending_request(self):
        action, msg = _handle_repl_command(SimpleNamespace(), "/approve")

        assert action == "approve"
        assert msg == "No pending dangerous request to approve."

        action, msg = _handle_repl_command(SimpleNamespace(), "/deny")

        assert action == "deny"
        assert msg == "No pending dangerous request to deny."

    def test_handle_repl_command_approve_next_reports_missing_session_state(self):
        action, msg = _handle_repl_command(SimpleNamespace(), "/approve_next")

        assert action == "approve_next"
        assert msg == "Approve-next unavailable: session approval state not wired."

    @pytest.mark.parametrize(
        ("command", "expected_action", "expected_msg"),
        [
            ("/approve extra", "approve", "Usage: /approve"),
            ("/deny extra", "deny", "Usage: /deny"),
            ("/approve_next extra", "approve_next", "Usage: /approve_next"),
        ],
    )
    def test_handle_repl_command_malformed_approve_commands_stay_local(self, command, expected_action, expected_msg):
        action, msg = _handle_repl_command(SimpleNamespace(), command)

        assert action == expected_action
        assert msg == expected_msg

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
        assert "Model: current or set provider/model" in msg
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
        assert msg.startswith("Jobs: showing=2 | active=1")
        assert "worker:sess-1" in msg
        assert "call:call-1" in msg

    def test_handle_repl_command_jobs_active_filters_non_terminal_jobs(self, monkeypatch):
        agent = SimpleNamespace(
            llm=SimpleNamespace(model="test"),
            config=SimpleNamespace(llm=SimpleNamespace(model="test")),
        )
        jobs = [
            SimpleNamespace(
                job_id="worker:sess-1",
                kind="worker_session",
                status="running",
                summary="Still going",
                last_update_at="2026-02-24T00:00:10Z",
            ),
            SimpleNamespace(
                job_id="call:call-1",
                kind="call_mission",
                status="queued",
                summary="Waiting to start",
                last_update_at="2026-02-24T00:00:09Z",
            ),
            SimpleNamespace(
                job_id="worker:sess-2",
                kind="worker_session",
                status="ok",
                summary="Done",
                last_update_at="2026-02-24T00:00:08Z",
            ),
        ]
        monkeypatch.setattr("archon.cli_repl_commands._collect_job_summaries", lambda limit=10: jobs)

        action, msg = _handle_repl_command(agent, "/jobs active 2")

        assert action == "jobs"
        assert msg.startswith("Jobs: showing=2 | active=2 | filter=active")
        assert "worker:sess-1" in msg
        assert "call:call-1" in msg
        assert "worker:sess-2" not in msg

    def test_handle_repl_command_jobs_active_includes_in_progress_research_jobs(self, monkeypatch):
        agent = SimpleNamespace(
            llm=SimpleNamespace(model="test"),
            config=SimpleNamespace(llm=SimpleNamespace(model="test")),
        )
        jobs = [
            SimpleNamespace(
                job_id="research:abc",
                kind="deep_research",
                status="in_progress",
                summary="Still researching",
                last_update_at="2026-03-06T22:05:00Z",
            ),
            SimpleNamespace(
                job_id="worker:sess-1",
                kind="worker_session",
                status="ok",
                summary="Done",
                last_update_at="2026-03-06T22:00:00Z",
            ),
        ]
        monkeypatch.setattr("archon.cli_repl_commands._collect_job_summaries", lambda limit=10: jobs)

        action, msg = _handle_repl_command(agent, "/jobs active")

        assert action == "jobs"
        assert msg.startswith("Jobs: showing=1 | active=1 | filter=active")
        assert "research:abc" in msg
        assert "worker:sess-1" not in msg

    def test_handle_repl_command_jobs_degrades_gracefully_on_store_error(self, monkeypatch):
        agent = SimpleNamespace(
            llm=SimpleNamespace(model="test"),
            config=SimpleNamespace(llm=SimpleNamespace(model="test")),
        )
        monkeypatch.setattr(
            "archon.cli_repl_commands._collect_job_summaries",
            lambda limit=10: (_ for _ in ()).throw(OSError("read-only file system")),
        )

        action, msg = _handle_repl_command(agent, "/jobs")

        assert action == "jobs"
        assert msg == "Jobs unavailable: read-only file system"

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

    def test_handle_repl_command_job_without_arg_shows_selector(self, monkeypatch):
        agent = SimpleNamespace(
            llm=SimpleNamespace(model="test"),
            config=SimpleNamespace(llm=SimpleNamespace(model="test")),
        )
        jobs = [
            SimpleNamespace(
                job_id="research:abc",
                kind="deep_research",
                status="in_progress",
                summary="Research in progress",
                last_update_at="2026-03-08T20:07:15Z",
            ),
            SimpleNamespace(
                job_id="worker:sess-1",
                kind="worker_session",
                status="error",
                summary="Worker session never started",
                last_update_at="2026-03-08T20:00:09Z",
            ),
        ]
        monkeypatch.setattr("archon.cli_repl_commands._collect_job_summaries", lambda limit=10: jobs)

        action, msg = _handle_repl_command(agent, "/job")

        assert action == "job"
        assert msg.startswith("Select a job:")
        assert "research:abc" in msg
        assert "worker:sess-1" in msg
        assert "Use /jobs show <job-id>" in msg

    def test_handle_repl_command_jobs_show_resolves_recent_job(self, monkeypatch):
        agent = SimpleNamespace(
            llm=SimpleNamespace(model="test"),
            config=SimpleNamespace(llm=SimpleNamespace(model="test")),
        )
        jobs = [
            SimpleNamespace(
                job_id="research:abc",
                kind="deep_research",
                status="in_progress",
                summary="Research in progress",
                last_update_at="2026-03-08T20:07:15Z",
            ),
            SimpleNamespace(
                job_id="worker:sess-1",
                kind="worker_session",
                status="error",
                summary="Worker session never started",
                last_update_at="2026-03-08T20:00:09Z",
            ),
        ]
        monkeypatch.setattr("archon.cli_repl_commands._collect_job_summaries", lambda limit=10: jobs)
        monkeypatch.setattr(
            "archon.cli_repl_commands.load_research_job",
            lambda interaction_id, refresh_client=None, hook_bus=None: SimpleNamespace(
                interaction_id=interaction_id,
                status="in_progress",
                summary="Research in progress",
                updated_at="2026-03-08T20:07:15Z",
                provider_status="in_progress",
                last_polled_at="",
                last_event_at="2026-03-08T20:07:15Z",
                stream_status="interaction.status_update",
                created_at="2026-03-08T20:07:15Z",
                latest_thought_summary="",
                output_text="",
                error="",
                poll_count=2,
                timeout_minutes=20,
            ),
        )

        action, msg = _handle_repl_command(agent, "/jobs show research:abc")

        assert action == "jobs"
        assert "job_id: research:abc" in msg

    def test_handle_repl_command_jobs_show_requires_job_id(self, monkeypatch):
        agent = SimpleNamespace(
            llm=SimpleNamespace(model="test"),
            config=SimpleNamespace(llm=SimpleNamespace(model="test")),
        )
        action, msg = _handle_repl_command(agent, "/jobs show")

        assert action == "jobs"
        assert msg == "Usage: /jobs show <job-id>"

    def test_handle_repl_command_jobs_includes_research_jobs(self, monkeypatch, tmp_path):
        from archon.research.models import ResearchJobRecord
        from archon.research.store import save_research_job

        monkeypatch.setattr("archon.research.store.RESEARCH_JOBS_DIR", tmp_path / "research" / "jobs")
        monkeypatch.setattr("archon.cli_repl_commands.list_worker_job_summaries", lambda limit=10: [])
        monkeypatch.setattr("archon.cli_repl_commands.list_call_job_summaries", lambda limit=10: [])
        save_research_job(
            ResearchJobRecord(
                interaction_id="abc",
                status="running",
                prompt="Research LA restaurant market",
                agent="deep-research-pro-preview-12-2025",
                created_at="2026-03-06T22:00:00Z",
                updated_at="2026-03-06T22:05:00Z",
                summary="LA market research started",
                output_text="",
                error="",
            )
        )
        agent = SimpleNamespace(
            llm=SimpleNamespace(model="test"),
            config=SimpleNamespace(llm=SimpleNamespace(model="test")),
        )

        action, msg = _handle_repl_command(agent, "/jobs")

        assert action == "jobs"
        assert "research:abc" in msg
        assert "LA market research started" in msg

    def test_handle_repl_command_job_loads_research_job_summary(self, monkeypatch, tmp_path):
        from archon.research.models import ResearchJobRecord
        from archon.research.store import save_research_job

        monkeypatch.setattr("archon.research.store.RESEARCH_JOBS_DIR", tmp_path / "research" / "jobs")
        monkeypatch.setattr("archon.cli_repl_commands.load_worker_job_summary", lambda _ref: None)
        monkeypatch.setattr("archon.cli_repl_commands.load_call_job_summary", lambda _ref: None)
        save_research_job(
            ResearchJobRecord(
                interaction_id="abc",
                status="done",
                prompt="Research LA restaurant market",
                agent="deep-research-pro-preview-12-2025",
                created_at="2026-03-06T22:00:00Z",
                updated_at="2026-03-06T22:10:00Z",
                summary="Completed",
                output_text="done",
                error="",
            )
        )
        agent = SimpleNamespace(
            llm=SimpleNamespace(model="test"),
            config=SimpleNamespace(llm=SimpleNamespace(model="test")),
        )

        action, msg = _handle_repl_command(agent, "/job research:abc")

        assert action == "job"
        assert "job_id: research:abc" in msg
        assert "job_kind: deep_research" in msg
        assert "job_summary: Completed" in msg

    def test_handle_repl_command_job_reads_local_research_record_without_refresh(self, monkeypatch, tmp_path):
        from archon.research.models import ResearchJobRecord
        from archon.research.store import save_research_job

        monkeypatch.setattr("archon.research.store.RESEARCH_JOBS_DIR", tmp_path / "research" / "jobs")
        monkeypatch.setattr("archon.cli_repl_commands.load_worker_job_summary", lambda _ref: None)
        monkeypatch.setattr("archon.cli_repl_commands.load_call_job_summary", lambda _ref: None)
        save_research_job(
            ResearchJobRecord(
                interaction_id="abc",
                status="completed",
                prompt="Research LA restaurant market",
                agent="deep-research-pro-preview-12-2025",
                created_at="2026-03-06T22:00:00Z",
                updated_at="2026-03-06T22:10:00Z",
                summary="Final report body",
                output_text="Final report body",
                error="",
                provider_status="completed",
                last_event_at="2026-03-06T22:10:00Z",
                stream_status="interaction.complete",
            )
        )
        agent = SimpleNamespace(
            llm=SimpleNamespace(model="test"),
            config=SimpleNamespace(llm=SimpleNamespace(model="test")),
        )

        action, msg = _handle_repl_command(agent, "/job research:abc")

        assert action == "job"
        assert "job_status: completed" in msg
        assert "job_summary: Final report body" in msg

    def test_handle_repl_command_job_formats_live_research_workflow_details(self, monkeypatch):
        record = SimpleNamespace(
            interaction_id="abc",
            status="in_progress",
            summary="Research in progress",
            updated_at="2026-03-06T22:10:00Z",
            created_at="2026-03-06T22:00:00Z",
            output_text="",
            error="",
            provider_status="in_progress",
            last_polled_at="2026-03-06T22:10:05Z",
            last_event_at="2026-03-06T22:10:06Z",
            stream_status="content.delta",
            latest_thought_summary="Checking sources",
            event_count=2,
            poll_count=3,
        )
        monkeypatch.setattr(
            "archon.cli_repl_commands.load_research_job",
            lambda interaction_id, refresh_client=None, hook_bus=None: record,
        )
        monkeypatch.setattr("archon.cli_repl_commands.load_worker_job_summary", lambda _ref: None)
        monkeypatch.setattr("archon.cli_repl_commands.load_call_job_summary", lambda _ref: None)

        cfg = Config()
        cfg.research.google_deep_research.timeout_minutes = 999999
        agent = SimpleNamespace(
            llm=SimpleNamespace(model="test"),
            config=cfg,
        )

        action, msg = _handle_repl_command(agent, "/job research:abc")

        assert action == "job"
        assert "job_id: research:abc" in msg
        assert "job_status: in_progress" in msg
        assert "job_provider_status: in_progress" in msg
        assert "job_last_polled_at: 2026-03-06T22:10:05Z" in msg
        assert "job_event_count: 2" in msg
        assert "job_poll_count: 3" in msg
        assert "job_elapsed:" in msg
        assert "job_live_status: stream active | waiting for next progress" in msg
        assert "job_stream_age:" in msg

    def test_handle_repl_command_job_marks_overdue_research_as_not_running_normally(self, monkeypatch):
        cfg = Config()
        cfg.research.google_deep_research.timeout_minutes = 20
        record = SimpleNamespace(
            interaction_id="abc",
            status="in_progress",
            summary="Research in progress",
            updated_at="2026-03-06T22:10:00Z",
            created_at="2000-03-06T22:00:00Z",
            output_text="",
            error="",
            provider_status="in_progress",
            last_polled_at="2026-03-06T22:10:05Z",
            last_event_at="2026-03-06T22:10:06Z",
            stream_status="content.delta",
            latest_thought_summary="Checking sources",
            poll_count=3,
        )
        monkeypatch.setattr(
            "archon.cli_repl_commands.load_research_job",
            lambda interaction_id, refresh_client=None, hook_bus=None: record,
        )
        monkeypatch.setattr("archon.cli_repl_commands.load_worker_job_summary", lambda _ref: None)
        monkeypatch.setattr("archon.cli_repl_commands.load_call_job_summary", lambda _ref: None)

        agent = SimpleNamespace(
            llm=SimpleNamespace(model="test"),
            config=cfg,
        )

        action, msg = _handle_repl_command(agent, "/job research:abc")

        assert action == "job"
        assert "job_live_status: stream active | running longer than configured 20m timeout" in msg

    def test_handle_repl_command_job_degrades_gracefully_on_lookup_error(self, monkeypatch):
        agent = SimpleNamespace(
            llm=SimpleNamespace(model="test"),
            config=SimpleNamespace(llm=SimpleNamespace(model="test")),
        )
        monkeypatch.setattr(
            "archon.cli_repl_commands._load_job_summary",
            lambda job_ref: (_ for _ in ()).throw(OSError("read-only file system")),
        )

        action, msg = _handle_repl_command(agent, "/job worker:sess-1")

        assert action == "job"
        assert msg == "Job unavailable: read-only file system"

    def test_handle_repl_command_job_cancel_reports_remote_cancel_failure_and_local_cancel(self, monkeypatch):
        class _CancelClient:
            def cancel_research(self, interaction_id: str):
                assert interaction_id == "abc"
                raise RuntimeError("provider cancel failed")

        monkeypatch.setattr(
            "archon.cli_repl_commands.cancel_research_job",
            lambda interaction_id, reason="": SimpleNamespace(status="cancelled"),
        )
        agent = SimpleNamespace(
            llm=SimpleNamespace(model="test"),
            config=SimpleNamespace(llm=SimpleNamespace(model="test")),
            _create_google_deep_research_client=lambda: _CancelClient(),
        )

        action, msg = _handle_repl_command(agent, "/job cancel research:abc")

        assert action == "job"
        assert "Marked local record cancelled for research:abc." in msg
        assert "Remote cancellation failed: RuntimeError: provider cancel failed" in msg

    def test_handle_repl_command_job_cancel_reports_remote_and_local_success(self, monkeypatch):
        class _CancelClient:
            def cancel_research(self, interaction_id: str):
                assert interaction_id == "abc"
                return SimpleNamespace(status="cancelled")

        monkeypatch.setattr(
            "archon.cli_repl_commands.cancel_research_job",
            lambda interaction_id, reason="": SimpleNamespace(status="cancelled"),
        )
        agent = SimpleNamespace(
            llm=SimpleNamespace(model="test"),
            config=SimpleNamespace(llm=SimpleNamespace(model="test")),
            _create_google_deep_research_client=lambda: _CancelClient(),
        )

        action, msg = _handle_repl_command(agent, "/job cancel research:abc")

        assert action == "job"
        assert msg == "Cancelled research:abc remotely and locally."

    def test_handle_repl_command_jobs_reads_local_research_job_state(self, monkeypatch):
        fresh_job = SimpleNamespace(
            job_id="research:abc",
            kind="deep_research",
            status="completed",
            summary="Final report body",
            last_update_at="2026-03-06T22:10:00Z",
        )
        monkeypatch.setattr("archon.cli_repl_commands._collect_job_summaries", lambda limit=10: [fresh_job])
        agent = SimpleNamespace(
            llm=SimpleNamespace(model="test"),
            config=SimpleNamespace(llm=SimpleNamespace(model="test")),
        )

        action, msg = _handle_repl_command(agent, "/jobs")

        assert action == "jobs"
        assert "completed" in msg
        assert "Final report body" in msg

    def test_handle_repl_command_jobs_skips_deep_research_client_when_no_research_jobs(self, monkeypatch):
        jobs = [
            SimpleNamespace(
                job_id="worker:sess-1",
                kind="worker_session",
                status="running",
                summary="Still going",
                last_update_at="2026-02-24T00:00:10Z",
            ),
        ]
        monkeypatch.setattr("archon.cli_repl_commands._collect_job_summaries", lambda limit=10: jobs)
        calls = []
        agent = SimpleNamespace(
            llm=SimpleNamespace(model="test"),
            config=SimpleNamespace(llm=SimpleNamespace(model="test")),
            _create_google_deep_research_client=lambda: calls.append("created") or object(),
        )

        action, msg = _handle_repl_command(agent, "/jobs")

        assert action == "jobs"
        assert calls == []
        assert "worker:sess-1" in msg

    def test_handle_repl_command_jobs_purge_reports_local_records(self, monkeypatch):
        monkeypatch.setattr("archon.cli_repl_commands.purge_completed_jobs", lambda statuses=None: 2)
        monkeypatch.setattr("archon.cli_repl_commands.purge_stale_sessions", lambda statuses=None: 1)
        agent = SimpleNamespace(
            llm=SimpleNamespace(model="test"),
            config=SimpleNamespace(llm=SimpleNamespace(model="test")),
        )

        action, msg = _handle_repl_command(agent, "/jobs purge")

        assert action == "jobs"
        assert msg == "Purged 3 local records (2 research, 1 worker)."

    def test_handle_repl_command_mcp_reports_enabled_counts_and_server_names(self):
        cfg = Config()
        cfg.mcp.servers = {
            "docs": MCPServerConfig(
                enabled=True,
                mode="read_only",
                transport="stdio",
                command=["python", "server.py"],
            ),
            "local": MCPServerConfig(
                enabled=False,
                mode="read_only",
                transport="stdio",
                command=["uvx", "local-server"],
            ),
        }
        agent = SimpleNamespace(
            llm=SimpleNamespace(model="test"),
            config=cfg,
        )

        action, msg = _handle_repl_command(agent, "/mcp")

        assert action == "mcp"
        assert msg.startswith("MCP: enabled=1/2")
        assert "servers=docs" in msg
        assert "/mcp show <server>" in msg

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

    def test_handle_repl_command_mcp_show_reports_server_details(self):
        cfg = Config()
        cfg.mcp.servers = {
            "docs": MCPServerConfig(
                enabled=True,
                mode="read_only",
                transport="stdio",
                command=["python", "server.py"],
                env={"EXA_API_KEY": "${EXA_API_KEY}"},
            )
        }
        agent = SimpleNamespace(
            llm=SimpleNamespace(model="test"),
            config=cfg,
        )

        action, msg = _handle_repl_command(agent, "/mcp show docs")

        assert action == "mcp"
        assert "MCP server: docs" in msg
        assert "enabled: on" in msg
        assert "mode: read_only" in msg
        assert "transport: stdio" in msg
        assert "command: python server.py" in msg
        assert "env_keys: EXA_API_KEY" in msg

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
        assert msg == "Cost: chat_session_tokens=150 | workflow_total_tokens=150 | input=120 | output=30"

    def test_handle_repl_command_cost_prefers_current_session_usage_totals(self, monkeypatch):
        agent = self._make_local_command_agent()
        agent.session_id = "sess-current"

        monkeypatch.setattr(
            "archon.cli_repl_commands.summarize_usage_for_session",
            lambda session_id: {
                "session_id": session_id,
                "input_tokens": 180,
                "output_tokens": 40,
                "total_tokens": 220,
                "event_count": 2,
            },
        )
        action, msg = _handle_repl_command(agent, "/cost")

        assert action == "cost"
        assert msg == "Cost: chat_session_tokens=150 | workflow_total_tokens=220 | input=120 | output=30"

    def test_handle_repl_command_doctor_reports_compact_health_summary(self):
        agent = self._make_local_command_agent()

        action, msg = _handle_repl_command(agent, "/doctor")

        assert action == "doctor"
        assert msg == "Doctor: llm=ok | profile=ok | calls=on | mcp=1/2"

    def test_handle_repl_command_permissions_reports_active_profile_permissions(self):
        agent = self._make_local_command_agent()

        action, msg = _handle_repl_command(agent, "/permissions")

        assert action == "permissions"
        assert msg == (
            "Permissions: permission_mode=confirm_all | profile=safe | mode=review | tools=2 [read_file,shell]"
        )

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
        assert "orchestrator=hybrid(shared-executor-routing)" in msg
        assert "calls=on" in msg
        assert "mcp=1/1" in msg
        assert "tokens=150" in msg

    def test_handle_repl_command_status_describes_hybrid_as_shared_executor_routing(self):
        cfg = Config()
        cfg.orchestrator.enabled = True
        cfg.orchestrator.mode = "hybrid"
        cfg.calls.enabled = True
        agent = SimpleNamespace(
            llm=SimpleNamespace(provider="google", model="gemini-x"),
            total_input_tokens=120,
            total_output_tokens=30,
            policy_profile="safe",
            config=cfg,
        )

        action, msg = _handle_repl_command(agent, "/status")

        assert action == "status"
        assert "orchestrator=hybrid(shared-executor-routing)" in msg

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
        assert "permission_mode=confirm_all" in msg
        assert "profile=safe" in msg
        assert "mode=review" in msg
        assert "tools=1 [memory_read]" in msg

    def test_handle_repl_command_permissions_can_set_mode(self):
        agent = self._make_local_command_agent()

        action, msg = _handle_repl_command(agent, "/permissions accept_reads")

        assert action == "permissions"
        assert msg == (
            "Permissions: permission_mode=accept_reads | profile=safe | mode=review | tools=2 [read_file,shell]"
        )
        assert agent.config.safety.permission_mode == "accept_reads"

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
        assert "permission_mode=confirm_all" in permissions_msg
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
            "Permissions: permission_mode=confirm_all | profile=safe | skill=coder | mode=review | tools=2 [read_file,shell]"
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

    def test_handle_repl_command_compact_reports_artifact_summary(self):
        class _CompactAgent:
            def __init__(self):
                self.llm = SimpleNamespace(provider="openai", model="gpt-5-mini")
                self.config = Config()
                self.history = [
                    {"role": "user", "content": "hello"},
                    {"role": "assistant", "content": "hi"},
                ]
                self._pending_compactions = []

            def compact_context(self):
                self.history = []
                self._pending_compactions = [
                    {
                        "path": "compactions/sessions/history-manual.md",
                        "layer": "session",
                        "summary": "user: hello",
                    }
                ]
                return {
                    "compacted_messages": 2,
                    "path": "compactions/sessions/history-manual.md",
                    "summary": "user: hello",
                }

        agent = _CompactAgent()

        action, msg = _handle_repl_command(agent, "/compact")

        assert action == "compact"
        assert msg == (
            "Compact: history_messages=2 | path=compactions/sessions/history-manual.md | summary=user: hello"
        )

    def test_handle_repl_command_context_reports_history_and_pending_compactions(self):
        agent = self._make_local_command_agent()
        agent.history = [{"role": "user", "content": "hello"}]
        agent._pending_compactions = [{"path": "compactions/sessions/history-manual.md"}]

        action, msg = _handle_repl_command(agent, "/context")

        assert action == "context"
        assert msg == "Context: history_messages=1 | pending_compactions=1"

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

    def test_chat_cmd_echoes_picker_selected_slash_command(self):
        class _FailIfRunAgent:
            def __init__(self):
                cfg = Config()
                cfg.llm.provider = "openai"
                cfg.llm.model = "gpt-5-mini"
                cfg.llm.api_key = "test-key"
                self.hooks = HookBus()
                self.config = cfg
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

        outputs = []
        agent = _FailIfRunAgent()
        inputs = iter(["/", "quit"])
        session_ids = iter(["sess-1", "sess-2"])

        _chat_cmd(
            make_agent_fn=lambda: agent,
            make_telegram_adapter_fn=lambda _cfg: None,
            new_session_id_fn=lambda: next(session_ids),
            save_exchange_fn=lambda *_args: None,
            slash_completer_fn=lambda *_args: None,
            pick_slash_command_fn=lambda: "/status",
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
        assert "you> /status" in plain
        assert any(text.startswith("Status:") for text in plain)

    def test_chat_cmd_uses_filtered_picker_for_partial_slash_input(self):
        class _FailIfRunAgent:
            def __init__(self):
                cfg = Config()
                cfg.llm.provider = "openai"
                cfg.llm.model = "gpt-5-mini"
                cfg.llm.api_key = "test-key"
                self.hooks = HookBus()
                self.config = cfg
                self.total_input_tokens = 0
                self.total_output_tokens = 0
                self.log_label = ""
                self.policy_profile = "default"
                self.on_thinking = None
                self.on_tool_call = None

            def run(self, _text):
                raise AssertionError("agent.run should not be called for partial slash picker resolution")

            def reset(self):
                return None

        outputs = []
        agent = _FailIfRunAgent()
        inputs = iter(["/mo", "quit"])
        session_ids = iter(["sess-1", "sess-2"])
        picked_queries = []

        def pick_fn(query=None):
            picked_queries.append(query)
            return "/model"

        _chat_cmd(
            make_agent_fn=lambda: agent,
            make_telegram_adapter_fn=lambda _cfg: None,
            new_session_id_fn=lambda: next(session_ids),
            save_exchange_fn=lambda *_args: None,
            slash_completer_fn=lambda *_args: None,
            pick_slash_command_fn=pick_fn,
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
        assert picked_queries == ["/mo"]
        assert "you> /model" in plain
        assert any(text.startswith("Current model:") for text in plain)

    def test_chat_cmd_uses_live_slash_palette_result_without_old_picker(self):
        class _FailIfRunAgent:
            def __init__(self):
                cfg = Config()
                cfg.llm.provider = "openai"
                cfg.llm.model = "gpt-5-mini"
                cfg.llm.api_key = "test-key"
                self.hooks = HookBus()
                self.config = cfg
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

        outputs = []
        agent = _FailIfRunAgent()
        session_ids = iter(["sess-1", "sess-2"])
        reads = iter([("/status", True), ("quit", False)])

        def read_interactive_input_fn(*, prompt, fallback_read_fn):
            assert prompt == "you>"
            return next(reads)

        _chat_cmd(
            make_agent_fn=lambda: agent,
            make_telegram_adapter_fn=lambda _cfg: None,
            new_session_id_fn=lambda: next(session_ids),
            save_exchange_fn=lambda *_args: None,
            slash_completer_fn=lambda *_args: None,
            pick_slash_command_fn=lambda *_args: (_ for _ in ()).throw(
                AssertionError("old picker should not be used")
            ),
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
            input_fn=lambda _prompt: "quit",
            readline_module=_FakeReadline(),
            time_time_fn=lambda: 0.0,
            version="test",
            read_interactive_input_fn=read_interactive_input_fn,
        )

        plain = [re.sub(r"\x1b\[[0-9;]*m", "", text) for text, _err in outputs]
        assert "you> /status" in plain
        assert any(text.startswith("Status:") for text in plain)

    def test_chat_cmd_uses_plain_prompt_when_runtime_prompt_is_noninteractive(self, monkeypatch):
        class _FailIfRunAgent:
            def __init__(self):
                cfg = Config()
                cfg.llm.provider = "openai"
                cfg.llm.model = "gpt-5-mini"
                cfg.llm.api_key = "test-key"
                self.hooks = HookBus()
                self.config = cfg
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

        monkeypatch.setattr("archon.cli.os.isatty", lambda _fd: False)
        monkeypatch.setattr("archon.cli.sys.stdin", SimpleNamespace(fileno=lambda: 0))
        monkeypatch.setattr("archon.cli.sys.stdout", SimpleNamespace(fileno=lambda: 1))

        outputs = []
        agent = _FailIfRunAgent()
        session_ids = iter(["sess-1", "sess-2"])
        reads = iter([("/status", True), ("quit", False)])

        def read_interactive_input_fn(*, prompt, fallback_read_fn):
            assert prompt == "you> "
            return next(reads)

        _chat_cmd(
            make_agent_fn=lambda: agent,
            make_telegram_adapter_fn=lambda _cfg: None,
            new_session_id_fn=lambda: next(session_ids),
            save_exchange_fn=lambda *_args: None,
            slash_completer_fn=lambda *_args: None,
            pick_slash_command_fn=lambda *_args: (_ for _ in ()).throw(
                AssertionError("old picker should not be used")
            ),
            is_bracketed_paste_start_fn=lambda _text: False,
            collect_bracketed_paste_fn=lambda *_args, **_kwargs: "",
            is_paste_command_fn=lambda _text: False,
            collect_paste_message_fn=lambda *_args, **_kwargs: "",
            handle_repl_command_fn=_handle_repl_command,
            is_model_runtime_error_fn=lambda _err: False,
            format_session_summary_fn=_format_session_summary,
            format_chat_response_fn=lambda text: text,
            format_turn_stats_fn=_format_turn_stats,
            make_readline_prompt_fn=_make_runtime_prompt,
            spinner_cls=_FakeSpinner,
            ansi_prompt_user="",
            ansi_error="",
            ansi_reset="",
            click_echo_fn=lambda text="", err=False: outputs.append((text, err)),
            input_fn=lambda _prompt: "quit",
            readline_module=_FakeReadline(),
            time_time_fn=lambda: 0.0,
            version="test",
            read_interactive_input_fn=read_interactive_input_fn,
        )

        plain = [re.sub(r"\x1b\[[0-9;]*m", "", text) for text, _err in outputs]
        assert "you> /status" in plain
        assert any(text.startswith("Status:") for text in plain)

    def test_chat_cmd_refreshes_slash_subvalues_from_agent_config(self):
        import archon.cli as cli_module

        original_subvalues = cli_module._SLASH_SUBVALUES.copy()

        class _Agent:
            def __init__(self):
                self.hooks = HookBus()
                self.config = Config()
                self.config.mcp.servers = {
                    "exa": MCPServerConfig(enabled=True, mode="read_only", transport="stdio")
                }
                self.total_input_tokens = 0
                self.total_output_tokens = 0
                self.log_label = ""
                self.policy_profile = "default"
                self.on_thinking = None
                self.on_tool_call = None

            def run(self, _text):
                raise AssertionError("agent.run should not be called for this test")

            def reset(self):
                return None

        agent = _Agent()
        inputs = iter(["quit"])
        session_ids = iter(["sess-1"])
        cli_module._SLASH_SUBVALUES = _build_slash_subvalues(Config())

        try:
            _chat_cmd(
                make_agent_fn=lambda: agent,
                make_telegram_adapter_fn=lambda _cfg: None,
                new_session_id_fn=lambda: next(session_ids),
                save_exchange_fn=lambda *_args: None,
                refresh_slash_subvalues_fn=lambda config: setattr(
                    cli_module,
                    "_SLASH_SUBVALUES",
                    _build_slash_subvalues(config),
                ),
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
                click_echo_fn=lambda *_args, **_kwargs: None,
                input_fn=lambda _prompt: next(inputs),
                readline_module=_FakeReadline(),
                time_time_fn=lambda: 0.0,
                version="test",
            )

            assert ("show exa", "Show one MCP server config") in cli_module._SLASH_SUBVALUES["/mcp"]
            assert ("show mcp:exa", "Show one MCP plugin") in cli_module._SLASH_SUBVALUES["/plugins"]
        finally:
            cli_module._SLASH_SUBVALUES = original_subvalues

    def test_chat_cmd_refreshes_slash_subvalues_before_each_prompt(self):
        import archon.cli as cli_module

        original_subvalues = cli_module._SLASH_SUBVALUES.copy()
        state = {"show_job": False}

        class _Agent:
            def __init__(self):
                self.hooks = HookBus()
                self.config = Config()
                self.total_input_tokens = 0
                self.total_output_tokens = 0
                self.log_label = ""
                self.policy_profile = "default"
                self.on_thinking = None
                self.on_tool_call = None

            def run(self, _text):
                raise AssertionError("agent.run should not be called for this test")

            def reset(self):
                return None

        agent = _Agent()
        inputs = iter(["/status", "quit"])
        session_ids = iter(["sess-1"])

        def _refresh(_config):
            cli_module._SLASH_SUBVALUES = {
                "/jobs": [("show research:abc", "Show one recent job")] if state["show_job"] else [],
            }

        def _handle(agent, text):
            if text == "/status":
                state["show_job"] = True
                return "status", "Status: ok"
            return _handle_repl_command(agent, text)

        try:
            _chat_cmd(
                make_agent_fn=lambda: agent,
                make_telegram_adapter_fn=lambda _cfg: None,
                new_session_id_fn=lambda: next(session_ids),
                save_exchange_fn=lambda *_args: None,
                refresh_slash_subvalues_fn=_refresh,
                slash_completer_fn=lambda *_args: None,
                pick_slash_command_fn=lambda: None,
                is_bracketed_paste_start_fn=lambda _text: False,
                collect_bracketed_paste_fn=lambda *_args, **_kwargs: "",
                is_paste_command_fn=lambda _text: False,
                collect_paste_message_fn=lambda *_args, **_kwargs: "",
                handle_repl_command_fn=_handle,
                is_model_runtime_error_fn=lambda _err: False,
                format_session_summary_fn=_format_session_summary,
                format_chat_response_fn=lambda text: text,
                format_turn_stats_fn=_format_turn_stats,
                make_readline_prompt_fn=lambda label, _ansi: label,
                spinner_cls=_FakeSpinner,
                ansi_prompt_user="",
                ansi_error="",
                ansi_reset="",
                click_echo_fn=lambda *_args, **_kwargs: None,
                input_fn=lambda _prompt: next(inputs),
                readline_module=_FakeReadline(),
                time_time_fn=lambda: 0.0,
                version="test",
            )

            assert ("show research:abc", "Show one recent job") in cli_module._SLASH_SUBVALUES["/jobs"]
        finally:
            cli_module._SLASH_SUBVALUES = original_subvalues

    def test_chat_cmd_wires_terminal_activity_feed_to_prompt_state(self):
        class _Agent:
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
                self.terminal_activity_feed = None

            def run(self, _text):
                raise AssertionError("agent.run should not be called for this test")

            def reset(self):
                return None

        class _FeedReadline(_FakeReadline):
            def get_line_buffer(self):
                return "partial input"

        agent = _Agent()
        captured = {}
        inputs = iter(["quit"])
        session_ids = iter(["sess-1"])

        class _RecordingFeed:
            def __init__(self, prompt_fn, input_fn):
                self._prompt_fn = prompt_fn
                self._input_fn = input_fn

            def emit(self, _event):
                captured["prompt"] = self._prompt_fn()
                captured["input"] = self._input_fn()

        def _make_feed(prompt_fn, input_fn):
            return _RecordingFeed(prompt_fn, input_fn)

        def _fake_input(_prompt):
            agent.terminal_activity_feed.emit(
                ActivityEvent(source="telegram", message="message received")
            )
            return next(inputs)

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
            make_readline_prompt_fn=lambda label, _ansi: f"{label} ",
            spinner_cls=_FakeSpinner,
            ansi_prompt_user="",
            ansi_error="",
            ansi_reset="",
            click_echo_fn=lambda *_args, **_kwargs: None,
            input_fn=_fake_input,
            readline_module=_FeedReadline(),
            time_time_fn=lambda: 0.0,
            version="test",
            make_terminal_activity_feed_fn=_make_feed,
        )

        assert captured == {"prompt": "you> ", "input": "partial input"}

    def test_chat_cmd_uses_transient_visible_input_for_terminal_feed(self):
        class _Agent:
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
                self.terminal_activity_feed = None

            def run(self, _text):
                raise AssertionError("agent.run should not be called for this test")

            def reset(self):
                return None

        class _FeedReadline(_FakeReadline):
            def get_line_buffer(self):
                return ""

        agent = _Agent()
        captured = {}
        session_ids = iter(["sess-1"])

        class _RecordingFeed:
            def __init__(self, prompt_fn, input_fn):
                self._prompt_fn = prompt_fn
                self._input_fn = input_fn

            def emit(self, _event):
                captured["prompt"] = self._prompt_fn()
                captured["input"] = self._input_fn()

        def _make_feed(prompt_fn, input_fn):
            return _RecordingFeed(prompt_fn, input_fn)

        def _fake_read_interactive_input(*, prompt, fallback_read_fn, set_visible_input_fn=None):
            assert prompt == "you> "
            assert callable(set_visible_input_fn)
            set_visible_input_fn("h")
            agent.terminal_activity_feed.emit(
                ActivityEvent(source="telegram", message="message received")
            )
            set_visible_input_fn(None)
            return "quit", False

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
            make_readline_prompt_fn=lambda label, _ansi: f"{label} ",
            spinner_cls=_FakeSpinner,
            ansi_prompt_user="",
            ansi_error="",
            ansi_reset="",
            click_echo_fn=lambda *_args, **_kwargs: None,
            input_fn=lambda _prompt: "quit",
            readline_module=_FeedReadline(),
            time_time_fn=lambda: 0.0,
            version="test",
            make_terminal_activity_feed_fn=_make_feed,
            read_interactive_input_fn=_fake_read_interactive_input,
        )

        assert captured == {"prompt": "you> ", "input": "h"}

    def test_chat_cmd_registers_terminal_feed_with_telegram_adapter(self):
        class _Agent:
            def __init__(self):
                self.hooks = HookBus()
                self.config = Config()
                self.config.llm.model = "test-model"
                self.config.telegram.enabled = True
                self.config.telegram.connect_on_chat = True
                self.config.telegram.allowed_user_ids = [42]
                self.config.telegram.poll_timeout_sec = 30
                self.total_input_tokens = 0
                self.total_output_tokens = 0
                self.log_label = ""
                self.policy_profile = "default"
                self.on_thinking = None
                self.on_tool_call = None

            def run(self, _text):
                raise AssertionError("agent.run should not be called for this test")

            def reset(self):
                return None

        class _Adapter:
            def __init__(self):
                self.activity_sink = None

            def start(self):
                return None

            def set_activity_sink(self, sink):
                self.activity_sink = sink

        agent = _Agent()
        adapter = _Adapter()
        inputs = iter(["quit"])
        session_ids = iter(["sess-1"])
        stderr = io.StringIO()

        def fake_input(_prompt):
            assert callable(adapter.activity_sink)
            adapter.activity_sink(ActivityEvent(source="telegram", message="received from 99: hello"))
            return next(inputs)

        with redirect_stderr(stderr):
            _chat_cmd(
                make_agent_fn=lambda: agent,
                make_telegram_adapter_fn=lambda _cfg: adapter,
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
                make_readline_prompt_fn=lambda label, _ansi: f"{label} ",
                spinner_cls=_FakeSpinner,
                ansi_prompt_user="",
                ansi_error="",
                ansi_reset="",
                click_echo_fn=lambda *_args, **_kwargs: None,
                input_fn=fake_input,
                readline_module=_FakeReadline("draft"),
                time_time_fn=lambda: 0.0,
                version="test",
            )

        assert "\r\033[K[telegram] received from 99: hello\r\nyou> draft" in stderr.getvalue()

    def test_chat_cmd_auto_activates_skill_from_explicit_request(self):
        class _Agent:
            def __init__(self):
                self.hooks = HookBus()
                self.config = Config()
                self.config.profiles = {"default": ProfileConfig()}
                self.config.llm.model = "test-model"
                self.total_input_tokens = 0
                self.total_output_tokens = 0
                self.log_label = ""
                self.policy_profile = "default"
                self.on_thinking = None
                self.on_tool_call = None
                self.run_calls = []

            def run(self, text):
                self.run_calls.append(text)
                self.total_input_tokens += 10
                self.total_output_tokens += 2
                return "ok"

            def reset(self):
                return None

        outputs = []
        agent = _Agent()
        inputs = iter(["use researcher skill to research LA restaurants", "quit"])
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
            make_readline_prompt_fn=lambda label, _ansi: f"{label} ",
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
        assert "Skill auto-activated: researcher" in plain
        assert agent.run_calls == ["use researcher skill to research LA restaurants"]
        assert agent.policy_profile == "__skill__:default:researcher"

    def test_auto_activate_skill_ignores_false_positive_phrase(self):
        agent = _LocalCommandAgent()

        changed, msg = _maybe_auto_activate_skill(agent, "my skillset needs work")

        assert (changed, msg) == (False, "")
        assert agent.policy_profile == "safe"

    def test_auto_activate_skill_does_not_trigger_on_use_sales_data_phrase(self):
        agent = _LocalCommandAgent()

        changed, msg = _maybe_auto_activate_skill(agent, "use sales data to research restaurant churn")

        assert (changed, msg) == (False, "")
        assert agent.policy_profile == "safe"

    def test_auto_activate_skill_ignores_quoted_meta_discussion(self):
        agent = _LocalCommandAgent()

        changed, msg = _maybe_auto_activate_skill(agent, 'What does "use researcher skill" do?')

        assert (changed, msg) == (False, "")
        assert agent.policy_profile == "safe"

class _FakeReadline:
    def __init__(self, line_buffer=""):
        self.line_buffer = line_buffer

    def set_completer(self, _fn):
        return None

    def set_completer_delims(self, _value):
        return None

    def parse_and_bind(self, _value):
        return None

    def get_line_buffer(self):
        return self.line_buffer


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
        self.session_id = ""
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

    def compact_context(self):
        history_messages = len(self.history)
        self.history = []
        self._pending_compactions = [
            {
                "path": "compactions/sessions/local-shell.md",
                "layer": "session",
                "summary": "assistant: local shell compaction",
            }
        ]
        return {
            "compacted_messages": history_messages,
            "path": "compactions/sessions/local-shell.md",
            "summary": "assistant: local shell compaction",
        }


class _DangerousActionAgent:
    def __init__(self, command_by_message):
        self.command_by_message = command_by_message
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
        self.tools = SimpleNamespace(confirmer=lambda _command, _level: False)
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.log_label = ""
        self.run_calls = []
        self.on_thinking = None
        self.on_tool_call = None

    def run(self, text):
        self.run_calls.append(text)
        command = self.command_by_message.get(text, text)
        if self.on_tool_call:
            self.on_tool_call("shell", {"command": command})
        allowed = self.tools.confirmer(command, Level.DANGEROUS)
        self.total_input_tokens += 10
        self.total_output_tokens += 2
        if not allowed:
            return "Command rejected by safety gate."
        return f"ran:{command}"

    def reset(self):
        return None


class _ReplayLeakAgent:
    def __init__(self):
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
        self.tools = SimpleNamespace(confirmer=lambda _command, _level: False)
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.log_label = ""
        self.run_calls = []
        self._danger_calls = 0
        self.on_thinking = None
        self.on_tool_call = None

    def run(self, text):
        self.run_calls.append(text)
        if text == "danger":
            self._danger_calls += 1
            if self._danger_calls == 1:
                if self.on_tool_call:
                    self.on_tool_call("shell", {"command": "rm important.txt"})
                allowed = self.tools.confirmer("rm important.txt", Level.DANGEROUS)
                self.total_input_tokens += 10
                self.total_output_tokens += 2
                if not allowed:
                    return "Command rejected by safety gate."
                return "unexpected pass"
            self.total_input_tokens += 10
            self.total_output_tokens += 2
            return "safe replay"

        if text == "later":
            if self.on_tool_call:
                self.on_tool_call("shell", {"command": "rm later.txt"})
            allowed = self.tools.confirmer("rm later.txt", Level.DANGEROUS)
            self.total_input_tokens += 10
            self.total_output_tokens += 2
            if not allowed:
                return "Command rejected by safety gate."
            return "ran:rm later.txt"

        self.total_input_tokens += 10
        self.total_output_tokens += 2
        return f"ok:{text}"

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
            ("/cost", "Cost: chat_session_tokens=150 | workflow_total_tokens=150 | input=120 | output=30"),
            ("/doctor", "Doctor: llm=ok | profile=ok | calls=on | mcp=1/2"),
            ("/permissions", "Permissions: permission_mode=confirm_all | profile=safe | mode=review | tools=2 [read_file,shell]"),
            ("/permissions status", "Permissions: permission_mode=confirm_all | profile=safe | mode=review | tools=2 [read_file,shell]"),
            ("/approvals", "Approvals: dangerous_mode=off | pending=none | approve_next_tokens=0"),
            ("/approvals status", "Approvals: dangerous_mode=off | pending=none | approve_next_tokens=0"),
            ("/approvals on", "Approvals: dangerous_mode=on | pending=none | approve_next_tokens=0"),
            ("/approvals off", "Approvals: dangerous_mode=off | pending=none | approve_next_tokens=0"),
            ("/approve", "No pending dangerous request to approve."),
            ("/approve extra", "Usage: /approve"),
            ("/deny", "No pending dangerous request to deny."),
            ("/deny extra", "Usage: /deny"),
            ("/approve_next", "Approvals: dangerous_mode=off | pending=none | approve_next_tokens=1"),
            ("/approve_next extra", "Usage: /approve_next"),
        ],
    )
    def test_local_shell_commands_do_not_call_agent_run(self, command, expected):
        agent = _LocalCommandAgent()

        outputs = [re.sub(r"\x1b\[[0-9;]*m", "", text) for text, _err in _run_local_command_session(agent, [command, "quit"])]

        assert expected in outputs
        assert agent.run_calls == []

    def test_local_shell_session_assigns_and_rotates_usage_session_id_on_reset(self):
        agent = _LocalCommandAgent()

        _run_local_command_session(agent, ["/status", "/reset", "/status", "quit"])

        assert agent.session_id == "sess-2"

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

    def test_local_context_commands_do_not_call_agent_run(self):
        agent = _LocalCommandAgent()
        agent.history = [{"role": "user", "content": "hello"}]

        outputs = [
            re.sub(r"\x1b\[[0-9;]*m", "", text)
            for text, _err in _run_local_command_session(
                agent,
                ["/context", "/compact", "/context", "quit"],
            )
        ]

        assert "Context: history_messages=1 | pending_compactions=0" in outputs
        assert "Compact: history_messages=1 | path=compactions/sessions/local-shell.md | summary=assistant: local shell compaction" in outputs
        assert "Context: history_messages=0 | pending_compactions=1" in outputs
        assert agent.run_calls == []

    def test_local_clear_command_does_not_call_agent_run(self):
        agent = _LocalCommandAgent()
        agent.history = [{"role": "user", "content": "hello"}]
        agent.total_input_tokens = 10
        agent.total_output_tokens = 5

        outputs = [
            re.sub(r"\x1b\[[0-9;]*m", "", text)
            for text, _err in _run_local_command_session(
                agent,
                ["/clear", "quit"],
            )
        ]

        assert "Cleared 1 messages. Fresh start." in outputs
        assert agent.run_calls == []
        assert agent.history == []
        assert agent.total_input_tokens == 0
        assert agent.total_output_tokens == 0


class TestCliPendingApprovalInteractiveChat:
    @staticmethod
    def _plain_outputs(outputs):
        return [re.sub(r"\x1b\[[0-9;]*m", "", text) for text, _err in outputs]

    def test_chat_pending_approval_state_is_exposed_to_status_commands(self):
        agent = _DangerousActionAgent({"danger": "rm important.txt"})

        outputs = self._plain_outputs(_run_local_command_session(agent, ["danger", "/approvals", "quit"]))

        approval_output = next(text for text in outputs if "approval required" in text.lower())
        assert "approval required: dangerous action blocked" in approval_output.lower()
        assert "request: rm important.txt" in approval_output
        assert "use /approve, /deny, /approve_next, or /approvals" in approval_output
        assert "Command rejected by safety gate." not in outputs
        assert "Approvals: dangerous_mode=off | pending=rm important.txt | approve_next_tokens=0" in outputs

        status = agent.get_terminal_approval_status()
        pending = status["pending_request"]
        assert pending["status"] == "pending"
        assert pending["blocked_command_preview"] == "rm important.txt"
        assert pending["blocked_user_input"] == "danger"

    def test_chat_pending_approval_replaces_the_previous_request(self):
        agent = _DangerousActionAgent(
            {
                "first": "rm first.txt",
                "second": "systemctl restart nginx",
            }
        )

        outputs = self._plain_outputs(_run_local_command_session(agent, ["first", "second", "/approvals", "quit"]))

        assert outputs.count("Command rejected by safety gate.") == 0
        assert "Approvals: dangerous_mode=off | pending=systemctl restart nginx | approve_next_tokens=0" in outputs

        status = agent.get_terminal_approval_status()
        pending = status["pending_request"]
        assert pending["status"] == "pending"
        assert pending["blocked_command_preview"] == "systemctl restart nginx"
        assert pending["blocked_user_input"] == "second"

    def test_chat_approvals_on_and_off_update_interactive_session_state(self):
        agent = _DangerousActionAgent({})

        outputs = self._plain_outputs(
            _run_local_command_session(agent, ["/approvals on", "/approvals", "/approvals off", "/approvals", "quit"])
        )

        assert outputs.count("Approvals: dangerous_mode=on | pending=none | approve_next_tokens=0") == 2
        assert outputs.count("Approvals: dangerous_mode=off | pending=none | approve_next_tokens=0") == 2
        assert agent.run_calls == []

    def test_chat_approve_next_updates_interactive_session_state(self):
        agent = _DangerousActionAgent({})

        outputs = self._plain_outputs(_run_local_command_session(agent, ["/approve_next", "/approvals", "quit"]))

        assert outputs.count("Approvals: dangerous_mode=off | pending=none | approve_next_tokens=1") == 2
        assert agent.run_calls == []

    def test_chat_approve_replays_pending_request_and_clears_state(self):
        agent = _DangerousActionAgent({"danger": "rm important.txt"})

        outputs = self._plain_outputs(_run_local_command_session(agent, ["danger", "/approve", "/approvals", "quit"]))

        assert "Pending dangerous request approved. Replaying request..." in outputs
        assert "ran:rm important.txt" in outputs
        assert "Approvals: dangerous_mode=off | pending=none | approve_next_tokens=0" in outputs
        assert agent.run_calls == ["danger", "danger"]

    def test_chat_approve_replay_does_not_leave_a_spare_dangerous_token(self):
        agent = _DangerousActionAgent(
            {
                "danger": "rm important.txt",
                "second": "rm second.txt",
            }
        )

        outputs = self._plain_outputs(
            _run_local_command_session(agent, ["danger", "/approve", "second", "/approvals", "quit"])
        )

        assert "ran:rm important.txt" in outputs
        assert any("approval required: dangerous action blocked" in text.lower() for text in outputs)
        assert "Approvals: dangerous_mode=off | pending=rm second.txt | approve_next_tokens=0" in outputs
        assert agent.run_calls == ["danger", "danger", "second"]

    def test_chat_approve_replay_does_not_leak_approval_when_replay_turn_is_safe(self):
        agent = _ReplayLeakAgent()

        outputs = self._plain_outputs(
            _run_local_command_session(agent, ["danger", "/approve", "later", "/approvals", "quit"])
        )

        assert "Pending dangerous request approved. Replaying request..." in outputs
        assert "safe replay" in outputs
        assert any("approval required: dangerous action blocked" in text.lower() for text in outputs)
        assert "Approvals: dangerous_mode=off | pending=rm later.txt | approve_next_tokens=0" in outputs
        assert agent.run_calls == ["danger", "danger", "later"]

    def test_chat_deny_clears_pending_state(self):
        agent = _DangerousActionAgent({"danger": "rm important.txt"})

        outputs = self._plain_outputs(
            _run_local_command_session(agent, ["danger", "/deny", "/approvals", "quit"])
        )

        assert "Denied pending dangerous request." in outputs
        assert "Approvals: dangerous_mode=off | pending=none | approve_next_tokens=0" in outputs
        assert agent.run_calls == ["danger"]

    def test_chat_approve_next_allows_exactly_one_dangerous_action(self):
        agent = _DangerousActionAgent(
            {
                "first": "rm first.txt",
                "second": "rm second.txt",
            }
        )

        outputs = self._plain_outputs(
            _run_local_command_session(agent, ["/approve_next", "first", "second", "/approvals", "quit"])
        )

        assert "ran:rm first.txt" in outputs
        assert any("approval required: dangerous action blocked" in text.lower() for text in outputs)
        assert "Approvals: dangerous_mode=off | pending=rm second.txt | approve_next_tokens=0" in outputs
        assert agent.run_calls == ["first", "second"]

    def test_chat_approvals_on_allows_dangerous_actions_until_turned_off(self):
        agent = _DangerousActionAgent(
            {
                "first": "rm first.txt",
                "second": "rm second.txt",
            }
        )

        outputs = self._plain_outputs(
            _run_local_command_session(
                agent,
                ["/approvals on", "first", "/approvals off", "second", "/approvals", "quit"],
            )
        )

        assert "ran:rm first.txt" in outputs
        assert any("approval required: dangerous action blocked" in text.lower() for text in outputs)
        assert "Approvals: dangerous_mode=off | pending=rm second.txt | approve_next_tokens=0" in outputs
        assert agent.run_calls == ["first", "second"]

    def test_local_control_commands_do_not_mutate_agent_history(self):
        agent = _LocalCommandAgent()

        _run_local_command_session(agent, ["/skills", "/plugins", "quit"])

        assert agent.history == []


class TestSlashCompleter:
    def test_matches_prefix(self):
        assert _slash_completer("/mo", 0) == "/model"
        assert _slash_completer("/mo", 1) is None

    def test_job_prefix_matches_job_commands(self):
        assert _slash_completer("/jo", 0) == "/jobs"
        assert _slash_completer("/jo", 1) is None

    def test_mcp_prefix_matches_command(self):
        assert _slash_completer("/mc", 0) == "/mcp"
        assert _slash_completer("/mc", 1) is None

    def test_skills_prefix_matches_command(self):
        assert _slash_completer("/sk", 0) == "/skills"
        assert _slash_completer("/sk", 1) is None

    def test_plugins_prefix_matches_command(self):
        assert _slash_completer("/pl", 0) == "/plugins"
        assert _slash_completer("/pl", 1) is None

    def test_compact_prefix_matches_command(self):
        assert _slash_completer("/co", 0) == "/cost"
        assert _slash_completer("/co", 1) == "/compact"
        assert _slash_completer("/co", 2) == "/context"
        assert _slash_completer("/co", 3) is None

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

    def test_profile_set_value_completion_from_line_buffer(self, monkeypatch):
        monkeypatch.setattr("archon.cli.readline.get_line_buffer", lambda: "/profile set ")
        assert _slash_completer("", 0) == "default"
        assert _slash_completer("", 1) is None

    def test_profile_set_value_completion_uses_live_profile_names(self, monkeypatch):
        cfg = Config()
        cfg.profiles = {
            "default": ProfileConfig(),
            "safe": ProfileConfig(allowed_tools=["memory_read"], max_mode="review"),
        }
        monkeypatch.setattr("archon.cli._SLASH_SUBVALUES", _build_slash_subvalues(cfg))
        monkeypatch.setattr("archon.cli.readline.get_line_buffer", lambda: "/profile set ")

        assert _slash_completer("", 0) == "default"
        assert _slash_completer("", 1) == "safe"
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

    def test_skills_show_value_completion_from_line_buffer(self, monkeypatch):
        monkeypatch.setattr("archon.cli.readline.get_line_buffer", lambda: "/skills show ")
        assert _slash_completer("", 0) == "coder"
        assert _slash_completer("", 1) == "general"
        assert _slash_completer("", 2) == "memory_curator"
        assert _slash_completer("", 3) == "operator"
        assert _slash_completer("", 4) == "researcher"
        assert _slash_completer("", 5) == "sales"
        assert _slash_completer("", 6) is None

    def test_skills_use_value_completion_from_line_buffer(self, monkeypatch):
        monkeypatch.setattr("archon.cli.readline.get_line_buffer", lambda: "/skills use ")
        assert _slash_completer("", 0) == "coder"
        assert _slash_completer("", 1) == "general"
        assert _slash_completer("", 2) == "memory_curator"
        assert _slash_completer("", 3) == "operator"
        assert _slash_completer("", 4) == "researcher"
        assert _slash_completer("", 5) == "sales"
        assert _slash_completer("", 6) is None

    def test_plugins_subcommand_completion_from_line_buffer(self, monkeypatch):
        monkeypatch.setattr("archon.cli.readline.get_line_buffer", lambda: "/plugins ")
        assert _slash_completer("", 0) == "list"
        assert _slash_completer("", 1) == "show"
        assert _slash_completer("", 2) is None

    def test_plugins_show_value_completion_from_line_buffer(self, monkeypatch):
        monkeypatch.setattr("archon.cli.readline.get_line_buffer", lambda: "/plugins show ")
        assert _slash_completer("", 0) == "calls"
        assert _slash_completer("", 1) == "telegram"
        assert _slash_completer("", 2) == "web"
        assert _slash_completer("", 3) is None

    def test_mcp_subcommand_completion_from_line_buffer(self, monkeypatch):
        monkeypatch.setattr("archon.cli.readline.get_line_buffer", lambda: "/mcp ")
        assert _slash_completer("", 0) == "servers"
        assert _slash_completer("", 1) == "show"
        assert _slash_completer("", 2) == "tools"
        assert _slash_completer("", 3) is None

    def test_mcp_show_value_completion_from_line_buffer(self, monkeypatch):
        monkeypatch.setattr("archon.cli.readline.get_line_buffer", lambda: "/mcp show ")
        assert _slash_completer("", 0) is None

    def test_jobs_subcommand_completion_from_line_buffer(self, monkeypatch):
        monkeypatch.setattr("archon.cli.readline.get_line_buffer", lambda: "/jobs ")
        assert _slash_completer("", 0) == "active"
        assert _slash_completer("", 1) == "all"
        assert _slash_completer("", 2) == "purge"
        assert _slash_completer("", 3) == "show"
        assert _slash_completer("", 4) is None

    def test_permissions_subcommand_completion_from_line_buffer(self, monkeypatch):
        monkeypatch.setattr("archon.cli.readline.get_line_buffer", lambda: "/permissions ")
        assert _slash_completer("", 0) == "status"
        assert _slash_completer("", 1) == "auto"
        assert _slash_completer("", 2) == "accept_reads"
        assert _slash_completer("", 3) == "confirm_all"
        assert _slash_completer("", 4) is None

    def test_model_subcommand_completion_from_line_buffer(self, monkeypatch):
        monkeypatch.setattr("archon.cli.readline.get_line_buffer", lambda: "/model ")
        assert _slash_completer("", 0) == "show"
        assert _slash_completer("", 1) == "set"
        assert _slash_completer("", 2) is None

    def test_model_set_value_completion_from_line_buffer(self, monkeypatch):
        monkeypatch.setattr("archon.cli.readline.get_line_buffer", lambda: "/model set ")
        assert _slash_completer("", 0) == "anthropic-claude-3-7-sonnet-20250219"
        assert _slash_completer("", 1) == "anthropic-claude-opus-4-1-20250805"
        assert _slash_completer("", 2) == "anthropic-claude-sonnet-4-20250514"


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

    def test_build_slash_subvalues_uses_live_mcp_server_names(self):
        cfg = Config()
        cfg.mcp.servers = {
            "exa": MCPServerConfig(enabled=True, mode="read_only", transport="stdio")
        }

        values = _build_slash_subvalues(cfg)

        assert ("show exa", "Show one MCP server config") in values["/mcp"]
        assert ("tools exa", "List advertised tools for one server") in values["/mcp"]
        assert all("docs" not in value for value, _desc in values["/mcp"])

    def test_build_slash_subvalues_uses_live_mcp_plugin_names(self):
        cfg = Config()
        cfg.mcp.servers = {
            "exa": MCPServerConfig(enabled=True, mode="read_only", transport="stdio")
        }

        values = _build_slash_subvalues(cfg)

        assert ("show mcp:exa", "Show one MCP plugin") in values["/plugins"]
        assert all("mcp:docs" not in value for value, _desc in values["/plugins"])

    def test_build_slash_subvalues_exposes_native_plugin_show_values(self):
        values = _build_slash_subvalues(Config())

        assert ("show calls", "Show one native plugin") in values["/plugins"]
        assert ("show telegram", "Show one native plugin") in values["/plugins"]
        assert ("show web", "Show one native plugin") in values["/plugins"]

    def test_build_slash_subvalues_exposes_recent_jobs_under_jobs_command(self, monkeypatch):
        monkeypatch.setattr(
            "archon.cli_commands.list_research_job_summaries",
            lambda limit=8: [
                SimpleNamespace(
                    job_id="research:abc",
                    kind="deep_research",
                    status="in_progress",
                    summary="Research in progress",
                    last_update_at="2026-03-08T20:07:15Z",
                )
            ],
        )
        monkeypatch.setattr("archon.cli_commands.list_worker_job_summaries", lambda limit=8: [])
        monkeypatch.setattr("archon.cli_commands.list_call_job_summaries", lambda limit=8: [])

        values = _build_slash_subvalues(Config())

        assert ("show research:abc", "Show one recent job") in values["/jobs"]

    def test_plugins_show_value_completion_uses_live_runtime_names(self, monkeypatch):
        cfg = Config()
        cfg.mcp.servers = {
            "exa": MCPServerConfig(enabled=True, mode="read_only", transport="stdio")
        }
        monkeypatch.setattr("archon.cli._SLASH_SUBVALUES", _build_slash_subvalues(cfg))
        monkeypatch.setattr("archon.cli.readline.get_line_buffer", lambda: "/plugins show ")

        matches = []
        idx = 0
        while True:
            match = _slash_completer("", idx)
            if match is None:
                break
            matches.append(match)
            idx += 1

        assert matches == ["calls", "telegram", "web", "mcp:exa"]

    def test_mcp_show_value_completion_uses_live_runtime_names(self, monkeypatch):
        cfg = Config()
        cfg.mcp.servers = {
            "exa": MCPServerConfig(enabled=True, mode="read_only", transport="stdio")
        }
        monkeypatch.setattr("archon.cli._SLASH_SUBVALUES", _build_slash_subvalues(cfg))
        monkeypatch.setattr("archon.cli.readline.get_line_buffer", lambda: "/mcp show ")

        assert _slash_completer("", 0) == "exa"
        assert _slash_completer("", 1) is None

    def test_job_value_completion_uses_recent_runtime_job_ids(self, monkeypatch):
        monkeypatch.setattr(
            "archon.cli_commands.list_worker_job_summaries",
            lambda limit=8: [SimpleNamespace(job_id="worker:abc", last_update_at="2026-03-07T10:00:00Z")],
        )
        monkeypatch.setattr(
            "archon.cli_commands.list_call_job_summaries",
            lambda limit=8: [SimpleNamespace(job_id="call:def", last_update_at="2026-03-07T09:00:00Z")],
        )
        monkeypatch.setattr(
            "archon.cli_commands.list_research_job_summaries",
            lambda limit=8: [SimpleNamespace(job_id="research:ghi", last_update_at="2026-03-07T11:00:00Z")],
        )
        monkeypatch.setattr("archon.cli._SLASH_SUBVALUES", _build_slash_subvalues(Config()))
        monkeypatch.setattr("archon.cli.readline.get_line_buffer", lambda: "/job ")

        assert _slash_completer("", 0) == "research:ghi"
        assert _slash_completer("", 1) == "worker:abc"
        assert _slash_completer("", 2) == "call:def"
        assert _slash_completer("", 3) is None

    def test_slash_subvalues_map(self):
        assert "/model" in _SLASH_SUBVALUES
        assert "/approvals" in _SLASH_SUBVALUES
        assert "/calls" in _SLASH_SUBVALUES
        assert "/permissions" in _SLASH_SUBVALUES
        assert "/profile" in _SLASH_SUBVALUES
        assert "/skills" in _SLASH_SUBVALUES
        assert "/plugins" in _SLASH_SUBVALUES
        model_values = [value for value, _desc in _SLASH_SUBVALUES["/model"]]
        assert model_values[:2] == ["show", "set"]
        approval_values = [value for value, _desc in _SLASH_SUBVALUES["/approvals"]]
        assert approval_values == ["status", "on", "off"]
        call_values = [value for value, _desc in _SLASH_SUBVALUES["/calls"]]
        assert call_values == ["status", "on", "off"]
        permission_values = [value for value, _desc in _SLASH_SUBVALUES["/permissions"]]
        assert permission_values == ["status", "auto", "accept_reads", "confirm_all"]
        profile_values = [value for value, _desc in _SLASH_SUBVALUES["/profile"]]
        assert profile_values == ["show", "set default"]
        skill_values = [value for value, _desc in _SLASH_SUBVALUES["/skills"]]
        assert skill_values[:3] == ["list", "show coder", "show general"]
        plugin_values = [value for value, _desc in _SLASH_SUBVALUES["/plugins"]]
        assert plugin_values == ["list", "show", "show calls", "show telegram", "show web"]

    def test_pick_slash_command_two_level(self, monkeypatch):
        picks = iter(["/calls", "on"])
        monkeypatch.setattr("archon.cli._run_picker", lambda *_a, **_k: next(picks))
        assert _pick_slash_command() == "/calls on"

    def test_pick_slash_command_filters_top_level_by_query(self, monkeypatch):
        seen = []

        def fake_run_picker(items, **_kwargs):
            seen.append([name for name, _desc in items])
            return "/model"

        monkeypatch.setattr("archon.cli._run_picker", fake_run_picker)

        assert _pick_slash_command("/mo") == "/model"
        assert seen[0] == ["/model"]

    def test_pick_slash_command_filters_subvalues_by_query(self, monkeypatch):
        cfg = Config()
        cfg.profiles = {
            "default": ProfileConfig(),
            "safe": ProfileConfig(allowed_tools=["memory_read"], max_mode="review"),
        }
        monkeypatch.setattr("archon.cli._SLASH_SUBVALUES", _build_slash_subvalues(cfg))
        seen = []

        def fake_run_picker(items, **_kwargs):
            seen.append([name for name, _desc in items])
            return "set safe"

        monkeypatch.setattr("archon.cli._run_picker", fake_run_picker)

        assert _pick_slash_command("/profile s") == "/profile set safe"
        assert seen[0] == ["show", "set default", "set safe"]

    def test_pick_slash_command_mcp_default_omits_incomplete_generic_verbs(self, monkeypatch):
        monkeypatch.setattr("archon.cli._SLASH_SUBVALUES", _build_slash_subvalues(Config()))
        seen = []

        def fake_run_picker(items, **_kwargs):
            seen.append([name for name, _desc in items])
            if len(seen) == 1:
                return "/mcp"
            return "servers"

        monkeypatch.setattr("archon.cli._run_picker", fake_run_picker)

        assert _pick_slash_command() == "/mcp servers"
        assert "servers" in seen[1]
        assert "show" not in seen[1]
        assert "tools" not in seen[1]

    def test_pick_slash_command_plugins_default_omits_incomplete_generic_show(self, monkeypatch):
        monkeypatch.setattr("archon.cli._SLASH_SUBVALUES", _build_slash_subvalues(Config()))
        seen = []

        def fake_run_picker(items, **_kwargs):
            seen.append([name for name, _desc in items])
            if len(seen) == 1:
                return "/plugins"
            return "list"

        monkeypatch.setattr("archon.cli._run_picker", fake_run_picker)

        assert _pick_slash_command() == "/plugins list"
        assert "list" in seen[1]
        assert "show" not in seen[1]
        assert "show calls" in seen[1]
        assert "show telegram" in seen[1]
        assert "show web" in seen[1]

    def test_pick_slash_command_mcp_runtime_omits_incomplete_generic_verbs(self, monkeypatch):
        cfg = Config()
        cfg.mcp.servers = {
            "exa": MCPServerConfig(enabled=True, mode="read_only", transport="stdio")
        }
        monkeypatch.setattr("archon.cli._SLASH_SUBVALUES", _build_slash_subvalues(cfg))
        seen = []

        def fake_run_picker(items, **_kwargs):
            seen.append([name for name, _desc in items])
            if len(seen) == 1:
                return "/mcp"
            return "show exa"

        monkeypatch.setattr("archon.cli._run_picker", fake_run_picker)

        assert _pick_slash_command() == "/mcp show exa"
        assert "show" not in seen[1]
        assert "tools" not in seen[1]
        assert "show exa" in seen[1]
        assert "tools exa" in seen[1]

    def test_pick_slash_command_plugins_runtime_omits_incomplete_generic_show(self, monkeypatch):
        cfg = Config()
        cfg.mcp.servers = {
            "exa": MCPServerConfig(enabled=True, mode="read_only", transport="stdio")
        }
        monkeypatch.setattr("archon.cli._SLASH_SUBVALUES", _build_slash_subvalues(cfg))
        seen = []

        def fake_run_picker(items, **_kwargs):
            seen.append([name for name, _desc in items])
            if len(seen) == 1:
                return "/plugins"
            return "show mcp:exa"

        monkeypatch.setattr("archon.cli._run_picker", fake_run_picker)

        assert _pick_slash_command() == "/plugins show mcp:exa"
        assert "show" not in seen[1]
        assert "show mcp:exa" in seen[1]
