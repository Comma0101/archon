"""CLI formatting helper tests."""

import re
from types import SimpleNamespace

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

    def test_format_session_summary(self):
        out = self._plain(_format_session_summary(3, 1200, 345))
        assert "Session: 3 turns" in out
        assert "1,200 in" in out
        assert "345 out" in out
        assert "1,545 total tokens" in out


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


class TestSlashCompleter:
    def test_matches_prefix(self):
        assert _slash_completer("/mo", 0) == "/model"
        assert _slash_completer("/mo", 1) == "/model-list"
        assert _slash_completer("/mo", 2) == "/model-set"
        assert _slash_completer("/mo", 3) is None

    def test_empty_returns_all(self):
        results = []
        for i in range(10):
            val = _slash_completer("", i)
            if val is None:
                break
            results.append(val)
        assert len(results) == len(_SLASH_COMMANDS)

    def test_no_match(self):
        assert _slash_completer("/xyz", 0) is None

    def test_slash_alone_matches_all(self):
        results = []
        for i in range(10):
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
        call_values = [value for value, _desc in _SLASH_SUBVALUES["/calls"]]
        assert call_values == ["status", "on", "off"]
        profile_values = [value for value, _desc in _SLASH_SUBVALUES["/profile"]]
        assert profile_values == ["show", "set default"]

    def test_pick_slash_command_two_level(self, monkeypatch):
        picks = iter(["/calls", "on"])
        monkeypatch.setattr("archon.cli._run_picker", lambda *_a, **_k: next(picks))
        assert _pick_slash_command() == "/calls on"
