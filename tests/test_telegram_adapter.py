"""Tests for Telegram adapter command routing."""

import threading
import time
from types import SimpleNamespace

from archon.adapters.telegram import TelegramAdapter, _TELEGRAM_BOT_COMMANDS
from archon.config import Config, MCPServerConfig, ProfileConfig
from archon.control.hooks import HookBus
from archon.news.models import NewsDigest, NewsRunResult
from archon.safety import Level
from archon.ux import events as ux_events


class _DummyAgent:
    def __init__(self):
        self.messages = []

    def run(self, text: str) -> str:
        self.messages.append(text)
        return "ok"

    def reset(self):
        self.messages.clear()


class _DummyTools:
    def __init__(self):
        self.confirmer = lambda command, level: True


class _DangerousAgent:
    def __init__(self):
        self.messages = []
        self.tools = _DummyTools()
        self.log_label = ""
        self.last_turn_id = ""
        self._turn_no = 0

    def run(self, text: str) -> str:
        self.messages.append(text)
        self._turn_no += 1
        self.last_turn_id = f"t{self._turn_no:03d}"
        if not self.tools.confirmer("pacman -Q | head", Level.DANGEROUS):
            return "Command rejected by safety gate."
        return "dangerous command output"

    def reset(self):
        self.messages.clear()


class _StreamingDangerousAgent(_DangerousAgent):
    def run(self, text: str) -> str:
        raise AssertionError(f"telegram chat should use run_stream for dangerous reply: {text}")

    def run_stream(self, text: str):
        self.messages.append(text)
        self._turn_no += 1
        self.last_turn_id = f"t{self._turn_no:03d}"
        if not self.tools.confirmer("pacman -Q | head", Level.DANGEROUS):
            yield "Command rejected by safety gate."
            return
        yield "dangerous command output"


class _JobRouteAgent:
    def __init__(self):
        self.messages = []
        self.hooks = HookBus()
        self.log_label = ""
        self.last_turn_id = ""

    def run(self, text: str) -> str:
        self.messages.append(text)
        self.last_turn_id = "t001"
        self.hooks.emit_kind(
            "orchestrator.route",
            task_id=self.last_turn_id,
            payload={
                "turn_id": self.last_turn_id,
                "lane": "job",
                "reason": "broad_scope_request",
                "surface": "telegram",
            },
        )
        return "ok"

    def reset(self):
        self.messages.clear()


class _TelegramLocalCommandAgent:
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
        self.on_thinking = None
        self.on_tool_call = None
        self.run_calls = []

    def run(self, text: str) -> str:
        self.run_calls.append(text)
        raise AssertionError(f"agent.run should not be called for local Telegram command: {text}")

    def reset(self):
        return None


class _SkillAwareAgent:
    def __init__(self):
        cfg = Config()
        cfg.llm.provider = "openai"
        cfg.llm.model = "gpt-5-mini"
        cfg.llm.api_key = "test-key"
        cfg.profiles = {"default": ProfileConfig()}
        self.hooks = HookBus()
        self.config = cfg
        self.llm = SimpleNamespace(provider="openai", model="gpt-5-mini")
        self.policy_profile = "default"
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.history = []
        self.log_label = ""
        self.on_thinking = None
        self.on_tool_call = None
        self.run_calls = []

    def run(self, text: str) -> str:
        self.run_calls.append(text)
        return "ok"

    def reset(self):
        return None


class _StreamingTelegramAgent(_TelegramLocalCommandAgent):
    def __init__(self, chunks: list[str]):
        super().__init__()
        self._chunks = list(chunks)
        self.run_stream_calls = []

    def run(self, text: str) -> str:
        raise AssertionError(f"telegram chat should use run_stream for final text: {text}")

    def run_stream(self, text: str):
        self.run_stream_calls.append(text)
        self.total_input_tokens += 12
        self.total_output_tokens += 4
        for chunk in self._chunks:
            yield chunk


def _adapter():
    return TelegramAdapter(
        token="123:abc",
        allowed_user_ids=[42],
        agent_factory=lambda: _DummyAgent(),
        poll_timeout_sec=1,
    )


class TestTelegramAdapterCommands:
    def test_activity_sink_emits_received_and_reply_notices(self, monkeypatch):
        adapter = _adapter()
        sent = []
        events = []

        monkeypatch.setattr("archon.adapters.telegram.new_session_id", lambda: "20260306-190000")
        monkeypatch.setattr(
            "archon.adapters.telegram.save_exchange",
            lambda session_id, user_msg, assistant_msg: None,
        )
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]
        adapter.set_activity_sink(lambda event: events.append((event.source, event.message)))

        adapter._handle_message({"text": "hello from telegram", "chat": {"id": 99}, "from": {"id": 42}})

        assert sent == [(99, "ok")]
        assert events[0] == ("telegram", "received from 99: hello from telegram")
        assert events[-1] == ("telegram", "replied to 99: ok")

    def test_activity_sink_redacts_and_sanitizes_received_message_preview(self, monkeypatch):
        adapter = _adapter()
        sent = []
        events = []

        monkeypatch.setattr("archon.adapters.telegram.new_session_id", lambda: "20260306-190002")
        monkeypatch.setattr(
            "archon.adapters.telegram.save_exchange",
            lambda session_id, user_msg, assistant_msg: None,
        )
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]
        adapter.set_activity_sink(lambda event: events.append((event.source, event.message)))

        adapter._handle_message(
            {
                "text": "hello OPENAI_API_KEY=sk-live \x1b[31mboom\x1b[0m",
                "chat": {"id": 99},
                "from": {"id": 42},
            }
        )

        assert sent == [(99, "ok")]
        assert events[0] == (
            "telegram",
            "received from 99: hello OPENAI_API_KEY=[REDACTED] boom",
        )

    def test_activity_sink_emits_blocked_approval_notice(self, monkeypatch):
        adapter = TelegramAdapter(
            token="123:abc",
            allowed_user_ids=[42],
            agent_factory=lambda: _DangerousAgent(),
            poll_timeout_sec=1,
        )
        sent = []
        events = []

        monkeypatch.setattr(adapter, "_send_typing", lambda chat_id: None)
        monkeypatch.setattr(
            "archon.adapters.telegram.save_exchange",
            lambda session_id, user_msg, assistant_msg: None,
        )
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]
        monkeypatch.setattr(
            adapter._bot,
            "send_message",
            lambda chat_id, text, **kwargs: sent.append((chat_id, text)) or {"message_id": 601},
        )
        adapter.set_activity_sink(lambda event: events.append((event.source, event.message)))

        adapter._handle_message({"text": "check system packages", "chat": {"id": 99}, "from": {"id": 42}})

        assert any(message == "approval blocked for 99: pacman -Q | head" for _source, message in events)

    def test_chat_agent_scans_activity_on_create(self, monkeypatch):
        adapter = TelegramAdapter(
            token="123:abc",
            allowed_user_ids=[42],
            agent_factory=lambda: _TelegramLocalCommandAgent(),
            poll_timeout_sec=1,
        )
        summary = object()
        calls = []

        monkeypatch.setattr(
            "archon.adapters.telegram._activity_scan_and_store",
            lambda config, activity_dir: calls.append((config, activity_dir)) or summary,
            raising=False,
        )

        agent = adapter._get_or_create_chat_agent(99)

        assert len(calls) == 1
        assert agent._activity_summary is summary

    def test_activity_command_renders_summary(self, monkeypatch):
        adapter = TelegramAdapter(
            token="123:abc",
            allowed_user_ids=[42],
            agent_factory=lambda: _TelegramLocalCommandAgent(),
            poll_timeout_sec=1,
        )
        sent = []

        monkeypatch.setattr("archon.adapters.telegram.save_exchange", lambda *_args: None)
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]
        monkeypatch.setattr(
            "archon.adapters.telegram.activity_summary_impl",
            lambda *, config, activity_dir, echo_fn: echo_fn("Recent activity line"),
            raising=False,
        )

        adapter._handle_message({"text": "/activity", "chat": {"id": 99}, "from": {"id": 42}})

        assert sent == [(99, "Recent activity line")]

    def test_help_mentions_activity_command(self):
        adapter = _adapter()
        sent = []
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]

        adapter._handle_message({"text": "/help", "chat": {"id": 99}, "from": {"id": 42}})

        assert sent
        assert "/activity" in sent[0][1]

    def test_bot_command_catalog_includes_activity(self):
        assert ("activity", "Show recent activity") in _TELEGRAM_BOT_COMMANDS

    def test_tool_ux_event_routes_only_to_matching_chat(self):
        adapter = TelegramAdapter(
            token="123:abc",
            allowed_user_ids=[42],
            agent_factory=lambda: _TelegramLocalCommandAgent(),
            poll_timeout_sec=1,
        )
        sent = []
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]
        hook_bus = HookBus()
        adapter.wire_hook_bus(hook_bus)

        agent_99 = adapter._get_or_create_chat_agent(99)
        adapter._get_or_create_chat_agent(100)
        event = ux_events.tool_end(
            "shell",
            "shell: exit 0 (1 lines)",
            session_id=agent_99.session_id,
        )

        hook_bus.emit_kind(
            "ux.tool_event",
            task_id="t001",
            payload={"event": event, "status": "completed"},
        )

        assert sent == [(99, "✓ shell: exit 0 (1 lines)")]

    def test_tool_output_lines_batch_until_tool_end(self):
        adapter = TelegramAdapter(
            token="123:abc",
            allowed_user_ids=[42],
            agent_factory=lambda: _TelegramLocalCommandAgent(),
            poll_timeout_sec=1,
        )
        sent = []
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]
        hook_bus = HookBus()
        adapter.wire_hook_bus(hook_bus)

        agent_99 = adapter._get_or_create_chat_agent(99)

        hook_bus.emit_kind(
            "ux.tool_event",
            task_id="t001",
            payload={
                "event": ux_events.tool_running(
                    tool="shell",
                    session_id=agent_99.session_id,
                    detail_type="output_line",
                    line="line1",
                )
            },
        )
        hook_bus.emit_kind(
            "ux.tool_event",
            task_id="t001",
            payload={
                "event": ux_events.tool_running(
                    tool="shell",
                    session_id=agent_99.session_id,
                    detail_type="output_line",
                    line="line2",
                )
            },
        )

        assert sent == []

        hook_bus.emit_kind(
            "ux.tool_event",
            task_id="t001",
            payload={
                "event": ux_events.tool_end(
                    "shell",
                    "shell: exit 0 (2 lines)",
                    session_id=agent_99.session_id,
                ),
                "status": "completed",
            },
        )

        assert len(sent) == 2
        assert sent[0][0] == 99
        assert sent[0][1].startswith("```")
        assert "line1" in sent[0][1]
        assert "line2" in sent[0][1]
        assert sent[1] == (99, "✓ shell: exit 0 (2 lines)")

    def test_explicit_skill_request_auto_activates_before_telegram_run(self, monkeypatch):
        adapter = TelegramAdapter(
            token="123:abc",
            allowed_user_ids=[42],
            agent_factory=lambda: _SkillAwareAgent(),
            poll_timeout_sec=1,
        )
        sent = []

        monkeypatch.setattr(adapter, "_send_typing", lambda chat_id: None)
        monkeypatch.setattr("archon.adapters.telegram.new_session_id", lambda: "20260306-190001")
        monkeypatch.setattr(
            "archon.adapters.telegram.save_exchange",
            lambda session_id, user_msg, assistant_msg: None,
        )
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]

        adapter._handle_message(
            {
                "text": "use researcher skill to research LA restaurants",
                "chat": {"id": 99},
                "from": {"id": 42},
            }
        )

        agent = adapter._agents[99]  # type: ignore[attr-defined]
        assert sent[0] == (99, "Skill auto-activated: researcher")
        assert sent[1] == (99, "ok")
        assert agent.run_calls == ["use researcher skill to research LA restaurants"]
        assert agent.policy_profile == "__skill__:default:researcher"

    def test_local_shell_parity_commands_are_handled_without_model_turn(self, monkeypatch):
        adapter = TelegramAdapter(
            token="123:abc",
            allowed_user_ids=[42],
            agent_factory=lambda: _TelegramLocalCommandAgent(),
            poll_timeout_sec=1,
        )
        sent = []
        saved = []

        monkeypatch.setattr("archon.adapters.telegram.new_session_id", lambda: "20260306-170000")
        monkeypatch.setattr(
            "archon.adapters.telegram.save_exchange",
            lambda session_id, user_msg, assistant_msg: saved.append((session_id, user_msg, assistant_msg)),
        )
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]

        commands = {
            "/status": "Status:",
            "/cost": "Cost:",
            "/doctor": "Doctor:",
            "/permissions": "Permissions:",
            "/skills": "Skills:",
            "/plugins": "Plugins:",
            "/mcp": "MCP:",
            "/profile": "Policy profile:",
        }

        for command, expected_prefix in commands.items():
            adapter._handle_message({"text": command, "chat": {"id": 99}, "from": {"id": 42}})
            assert sent[-1][0] == 99
            assert sent[-1][1].startswith(expected_prefix)
            assert saved[-1][0] == "tg-99-20260306-170000"
            assert saved[-1][1] == command
            assert saved[-1][2].startswith(expected_prefix)

        assert adapter._agents[99].session_id == "tg-99-20260306-170000"  # type: ignore[attr-defined]

        assert 99 in adapter._agents  # type: ignore[attr-defined]
        agent = adapter._agents[99]  # type: ignore[attr-defined]
        assert agent.run_calls == []

    def test_local_shell_parity_subcommands_are_handled_without_model_turn(self, monkeypatch):
        adapter = TelegramAdapter(
            token="123:abc",
            allowed_user_ids=[42],
            agent_factory=lambda: _TelegramLocalCommandAgent(),
            poll_timeout_sec=1,
        )
        sent = []

        monkeypatch.setattr("archon.adapters.telegram.new_session_id", lambda: "20260306-170001")
        monkeypatch.setattr(
            "archon.adapters.telegram.save_exchange",
            lambda session_id, user_msg, assistant_msg: None,
        )
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]

        commands = {
            "/profile show": "Policy profile:",
            "/skills show coder": "Skill coder:",
            "/plugins show mcp:docs": "Plugin mcp:docs:",
            "/mcp show docs": "MCP server: docs",
        }

        for command, expected_prefix in commands.items():
            adapter._handle_message({"text": command, "chat": {"id": 99}, "from": {"id": 42}})
            assert sent[-1][1].startswith(expected_prefix)

        agent = adapter._agents[99]  # type: ignore[attr-defined]
        assert agent.run_calls == []

    def test_local_context_command_reports_richer_pressure_snapshot(self, monkeypatch):
        def _agent_factory():
            agent = _TelegramLocalCommandAgent()
            agent.history = [{"role": "user", "content": "hello"}]
            agent._pending_compactions = [{"path": "compactions/sessions/history-manual.md"}]
            agent.last_input_tokens = 32000
            return agent

        adapter = TelegramAdapter(
            token="123:abc",
            allowed_user_ids=[42],
            agent_factory=_agent_factory,
            poll_timeout_sec=1,
        )
        sent = []

        monkeypatch.setattr("archon.adapters.telegram.new_session_id", lambda: "20260306-170002")
        monkeypatch.setattr(
            "archon.adapters.telegram.save_exchange",
            lambda session_id, user_msg, assistant_msg: None,
        )
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]

        adapter._handle_message({"text": "/context", "chat": {"id": 99}, "from": {"id": 42}})

        assert sent[-1][0] == 99
        assert sent[-1][1].startswith("Context:")
        assert "history_messages=1" in sent[-1][1]
        assert "pending_compactions=1" in sent[-1][1]
        assert "history_chars=" in sent[-1][1]
        assert "approx_history_tokens=" in sent[-1][1]
        assert "last_input_tokens=32000" in sent[-1][1]
        assert "pressure=high" in sent[-1][1]
        assert "recommend=" not in sent[-1][1]

        agent = adapter._agents[99]  # type: ignore[attr-defined]
        assert agent.run_calls == []

    def test_local_new_command_clears_history_without_model_turn(self, monkeypatch):
        def _agent_factory():
            agent = _TelegramLocalCommandAgent()
            agent.history = [{"role": "user", "content": "hello"}]
            agent.last_input_tokens = 32000
            return agent

        adapter = TelegramAdapter(
            token="123:abc",
            allowed_user_ids=[42],
            agent_factory=_agent_factory,
            poll_timeout_sec=1,
        )
        sent = []

        monkeypatch.setattr("archon.adapters.telegram.new_session_id", lambda: "20260306-170003")
        monkeypatch.setattr(
            "archon.adapters.telegram.save_exchange",
            lambda session_id, user_msg, assistant_msg: None,
        )
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]

        adapter._handle_message({"text": "/new", "chat": {"id": 99}, "from": {"id": 42}})
        adapter._handle_message({"text": "/context", "chat": {"id": 99}, "from": {"id": 42}})

        assert sent[0] == (99, "Cleared 1 messages. Fresh chat context in the same session.")
        assert sent[1][0] == 99
        assert sent[1][1].startswith("Context:")
        assert "history_messages=0" in sent[1][1]
        assert "pressure=ok" in sent[1][1]
        assert "recommend=" not in sent[1][1]

        agent = adapter._agents[99]  # type: ignore[attr-defined]
        assert agent.run_calls == []

    def test_regular_chat_messages_are_persisted_to_history(self, monkeypatch):
        adapter = _adapter()
        saved = []

        monkeypatch.setattr("archon.adapters.telegram.new_session_id", lambda: "20260225-070000")
        monkeypatch.setattr(
            "archon.adapters.telegram.save_exchange",
            lambda session_id, user_msg, assistant_msg: saved.append((session_id, user_msg, assistant_msg)),
        )
        adapter._send_text = lambda chat_id, text: None  # type: ignore[method-assign]

        adapter._handle_message({"text": "hello", "chat": {"id": 99}, "from": {"id": 42}})
        adapter._handle_message({"text": "again", "chat": {"id": 99}, "from": {"id": 42}})

        assert len(saved) == 2
        assert saved[0][0] == "tg-99-20260225-070000"
        assert saved[1][0] == "tg-99-20260225-070000"
        assert saved[0][1] == "hello"
        assert saved[1][1] == "again"
        assert saved[0][2] == "ok"

    def test_voice_message_is_transcribed_and_routed_through_agent(self, monkeypatch):
        adapter = _adapter()
        sent = []
        sent_voices = []
        saved = []
        events = []

        monkeypatch.setattr("archon.adapters.telegram.new_session_id", lambda: "20260225-080000")
        monkeypatch.setattr(
            "archon.adapters.telegram.save_exchange",
            lambda session_id, user_msg, assistant_msg: saved.append((session_id, user_msg, assistant_msg)),
        )
        monkeypatch.setattr(adapter, "_send_typing", lambda chat_id: None)
        monkeypatch.setattr(adapter._bot, "get_file", lambda file_id, timeout=10: {"file_path": "voice/a.ogg"})
        monkeypatch.setattr(adapter._bot, "download_file", lambda file_path, timeout=20: b"audio-data")
        monkeypatch.setattr(
            "archon.adapters.telegram.transcribe_audio_bytes",
            lambda data, mime_type: "what do you think about my system",
        )
        monkeypatch.setattr(
            "archon.adapters.telegram.synthesize_speech_wav",
            lambda text: (b"RIFF....WAVE", "audio/wav"),
        )
        monkeypatch.setattr(
            "archon.adapters.telegram.convert_wav_to_ogg_opus",
            lambda data: (b"OggS....", "audio/ogg"),
        )
        monkeypatch.setattr(
            adapter._bot,
            "send_voice_bytes",
            lambda chat_id, filename, data, **kwargs: sent_voices.append((chat_id, filename, data, kwargs))
            or {"message_id": 900},
        )
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]
        adapter.set_activity_sink(lambda event: events.append((event.source, event.message)))

        adapter._handle_message(
            {
                "voice": {"file_id": "f1", "mime_type": "audio/ogg"},
                "chat": {"id": 99},
                "from": {"id": 42},
            }
        )

        agent = adapter._agents[99]  # type: ignore[attr-defined]
        assert agent.messages == ["what do you think about my system"]
        assert sent == [(99, "ok")]
        assert saved
        assert saved[0][0] == "tg-99-20260225-080000"
        assert saved[0][1] == "[voice] what do you think about my system"
        assert saved[0][2] == "ok"
        assert sent_voices
        assert sent_voices[0][0] == 99
        assert sent_voices[0][1].endswith(".ogg")
        assert sent_voices[0][2].startswith(b"OggS")
        assert ("telegram", "voice reply sent to 99") in events

    def test_voice_message_tts_failure_does_not_break_text_reply(self, monkeypatch):
        adapter = _adapter()
        sent = []
        events = []
        monkeypatch.setattr(adapter, "_send_typing", lambda chat_id: None)
        monkeypatch.setattr(adapter._bot, "get_file", lambda file_id, timeout=10: {"file_path": "voice/a.ogg"})
        monkeypatch.setattr(adapter._bot, "download_file", lambda file_path, timeout=20: b"audio-data")
        monkeypatch.setattr(
            "archon.adapters.telegram.transcribe_audio_bytes",
            lambda data, mime_type: "hello",
        )
        monkeypatch.setattr(
            "archon.adapters.telegram.synthesize_speech_wav",
            lambda text: (_ for _ in ()).throw(RuntimeError("tts unavailable")),
        )
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]
        adapter.set_activity_sink(lambda event: events.append((event.source, event.message)))

        adapter._handle_message(
            {
                "voice": {"file_id": "f1", "mime_type": "audio/ogg"},
                "chat": {"id": 99},
                "from": {"id": 42},
            }
        )

        assert sent == [(99, "ok")]
        assert ("telegram", "voice reply failed for 99: RuntimeError: tts unavailable") in events

    def test_approvals_status_omits_expired_pending_request(self, monkeypatch):
        adapter = _adapter()
        sent = []
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]
        adapter._pending_approvals[99] = {  # type: ignore[attr-defined]
            "approval_id": "deadbeef",
            "status": "pending",
            "blocked_command_preview": "pacman -Q | head",
            "expires_at": 100.0,
        }
        monkeypatch.setattr("archon.adapters.telegram.time.time", lambda: 101.0)

        adapter._handle_message(
            {
                "text": "/approvals",
                "chat": {"id": 99},
                "from": {"id": 42},
            }
        )

        assert sent[-1][1] == (
            "Approvals: dangerous_mode=off | pending_request=none | allow_once_remaining=0 | "
            "replay=/approve | allow_once=/approve_next | deny=/deny"
        )
        assert adapter._pending_approvals.get(99) is None  # type: ignore[attr-defined]

    def test_approvals_status_reports_effective_dangerous_mode_while_elevated(self, monkeypatch):
        adapter = _adapter()
        sent = []
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]
        monkeypatch.setattr("archon.adapters.telegram.time.time", lambda: 100.0)
        adapter._approval_elevated_until[99] = 220.0  # type: ignore[attr-defined]

        adapter._handle_message(
            {
                "text": "/approvals",
                "chat": {"id": 99},
                "from": {"id": 42},
            }
        )

        assert sent[-1][1] == (
            "Approvals: dangerous_mode=on | pending_request=none | allow_once_remaining=0 | "
            "elevated_ttl_sec=120 | replay=/approve | allow_once=/approve_next | deny=/deny"
        )

    def test_approve_next_reports_effective_dangerous_mode_while_elevated(self, monkeypatch):
        adapter = _adapter()
        sent = []
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]
        monkeypatch.setattr("archon.adapters.telegram.time.time", lambda: 100.0)
        adapter._approval_elevated_until[99] = 220.0  # type: ignore[attr-defined]

        adapter._handle_message(
            {
                "text": "/approve_next",
                "chat": {"id": 99},
                "from": {"id": 42},
            }
        )

        assert (
            sent[-1][1]
            == "Approval: result=allow_once_armed | dangerous_mode=on | pending_request=none | "
            "allow_once_remaining=1 | next=one_future_dangerous_action_allowed | review=/approvals"
        )


def test_get_updates_retries_transient_connection_reset_once(monkeypatch):
    adapter = _adapter()
    calls = []

    def fake_api_call(method, payload, timeout=10):
        calls.append((method, payload, timeout))
        if len(calls) == 1:
            raise RuntimeError(
                "Telegram API getUpdates network error: [Errno 104] Connection reset by peer"
            )
        return {
            "ok": True,
            "result": [],
        }

    monkeypatch.setattr(adapter, "_api_call", fake_api_call)

    updates = adapter._get_updates()

    assert updates == []
    assert len(calls) == 2


def test_run_loop_disables_polling_after_get_updates_conflict(monkeypatch, capsys):
    adapter = _adapter()
    calls = []

    monkeypatch.setattr(adapter, "_sync_bot_commands", lambda: None)
    monkeypatch.setattr(adapter, "_sync_startup_offset", lambda: None)

    def fake_get_updates():
        calls.append("get_updates")
        raise RuntimeError(
            'Telegram API getUpdates HTTP 409: {"ok":false,"error_code":409,"description":"Conflict: terminated by other getUpdates request; make sure that only one bot instance is running"}'
        )

    monkeypatch.setattr(adapter, "_get_updates", fake_get_updates)

    adapter._run_loop()

    assert calls == ["get_updates"]
    assert adapter._polling_disabled_due_to_conflict is True
    err = capsys.readouterr().err
    assert "Polling disabled" in err
    assert "HTTP 409" in err


def test_transport_health_becomes_degraded_after_startup_sync_failure(monkeypatch):
    adapter = _adapter()

    monkeypatch.setattr(adapter, "_api_call", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("dns")))

    adapter._sync_startup_offset()

    health = adapter._transport_health_snapshot()
    assert health["state"] == "degraded"
    assert health["last_error_source"] == "startup_sync"


def test_transport_health_recovers_to_healthy_after_successful_get_updates(monkeypatch):
    adapter = _adapter()
    monkeypatch.setattr(adapter, "_api_call", lambda *_args, **_kwargs: {"ok": True, "result": []})

    adapter._sync_startup_offset()
    adapter._get_updates()

    health = adapter._transport_health_snapshot()
    assert health["state"] == "healthy"


def test_transport_health_marks_conflict_as_disabled(monkeypatch):
    adapter = _adapter()
    monkeypatch.setattr(adapter, "_sync_bot_commands", lambda: None)
    monkeypatch.setattr(adapter, "_sync_startup_offset", lambda: None)
    monkeypatch.setattr(
        adapter,
        "_get_updates",
        lambda: (_ for _ in ()).throw(
            RuntimeError(
                'Telegram API getUpdates HTTP 409: {"ok":false,"error_code":409,"description":"Conflict: terminated by other getUpdates request; make sure that only one bot instance is running"}'
            )
        ),
    )

    adapter._run_loop()

    health = adapter._transport_health_snapshot()
    assert health["state"] == "disabled_conflict"


def test_concurrent_get_or_create_chat_agent_returns_single_instance(monkeypatch):
    factory_calls = []
    adapter = TelegramAdapter(
        token="123:abc",
        allowed_user_ids=[42],
        agent_factory=lambda: factory_calls.append(object()) or time.sleep(0.05) or _TelegramLocalCommandAgent(),
        poll_timeout_sec=1,
    )
    agents = []
    errors = []
    start = threading.Barrier(4)

    def worker():
        try:
            start.wait(timeout=1)
            agents.append(adapter._get_or_create_chat_agent(99))
        except Exception as exc:  # pragma: no cover - assertion captures details below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert len({id(agent) for agent in agents}) == 1
    assert len(factory_calls) == 1


def test_stop_cancels_pending_batch_collectors():
    adapter = _adapter()
    cancelled = []

    class _FakeCollector:
        def cancel(self):
            cancelled.append("cancel")

    adapter._batch_collectors[99] = _FakeCollector()  # type: ignore[assignment]

    adapter.stop()

    assert cancelled == ["cancel"]
    assert adapter._batch_collectors == {}


def test_voice_message_voice_upload_falls_back_to_wav_document(monkeypatch):
    adapter = _adapter()
    sent = []
    sent_docs = []
    monkeypatch.setattr(adapter, "_send_typing", lambda chat_id: None)
    monkeypatch.setattr(adapter._bot, "get_file", lambda file_id, timeout=10: {"file_path": "voice/a.ogg"})
    monkeypatch.setattr(adapter._bot, "download_file", lambda file_path, timeout=20: b"audio-data")
    monkeypatch.setattr("archon.adapters.telegram.transcribe_audio_bytes", lambda data, mime_type: "hello")
    monkeypatch.setattr("archon.adapters.telegram.synthesize_speech_wav", lambda text: (b"RIFF....WAVE", "audio/wav"))
    monkeypatch.setattr(
        "archon.adapters.telegram.convert_wav_to_ogg_opus",
        lambda data: (_ for _ in ()).throw(RuntimeError("ffmpeg failed")),
    )
    monkeypatch.setattr(
        adapter._bot,
        "send_document_bytes",
        lambda chat_id, filename, data, **kwargs: sent_docs.append((chat_id, filename, data, kwargs))
        or {"message_id": 901},
    )
    adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]

    adapter._handle_message(
        {
            "voice": {"file_id": "f1", "mime_type": "audio/ogg"},
            "chat": {"id": 99},
            "from": {"id": 42},
        }
    )

    assert sent == [(99, "ok")]
    assert sent_docs
    assert sent_docs[0][1].endswith(".wav")


def test_chat_agent_wires_terminal_feed_proxy_from_activity_sink():
    events = []
    adapter = TelegramAdapter(
        token="token",
        allowed_user_ids=[42],
        agent_factory=_DummyAgent,
    )
    adapter.set_activity_sink(lambda event: events.append((event.source, event.message)))

    agent = adapter._get_or_create_chat_agent(99)
    agent.terminal_activity_feed.emit_text("[telegram chat=99 turn=t001] > news_brief")

    assert ("telegram", "[telegram chat=99 turn=t001] > news_brief") in events

    def test_voice_message_missing_file_id_returns_clear_error(self, monkeypatch):
        adapter = _adapter()
        sent = []
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]

        adapter._handle_message(
            {
                "voice": {"mime_type": "audio/ogg"},
                "chat": {"id": 99},
                "from": {"id": 42},
            }
        )

        assert sent
        assert "missing file_id" in sent[0][1].lower()
        assert 99 not in adapter._agents  # type: ignore[attr-defined]

    def test_help_mentions_local_shell_parity_commands(self):
        adapter = _adapter()
        sent = []
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]

        adapter._handle_message(
            {
                "text": "/help",
                "chat": {"id": 99},
                "from": {"id": 42},
            }
        )

        assert sent
        assert "Core: /status, /approvals, /jobs, /skills, /mcp, /reset" in sent[0][1]
        assert "Context: /new, /clear, /compact, /context, /cost" in sent[0][1]
        assert "Advanced:" in sent[0][1]
        assert "/plugins" in sent[0][1]
        assert "/jobs show <job-id>" in sent[0][1]
        assert "/job <id>" not in sent[0][1]

    def test_startup_sync_skips_pending_updates_by_advancing_offset(self, monkeypatch):
        adapter = _adapter()
        calls = []

        def fake_api_call(method, payload, timeout=10):
            calls.append((method, payload, timeout))
            assert method == "getUpdates"
            assert payload["timeout"] == 0
            return {
                "ok": True,
                "result": [
                    {"update_id": 101, "message": {"text": "old 1"}},
                    {"update_id": 105, "message": {"text": "old 2"}},
                ],
            }

        monkeypatch.setattr(adapter, "_api_call", fake_api_call)

        adapter._sync_startup_offset()

        assert adapter._offset == 106
        assert len(calls) == 1

    def test_startup_sync_retries_after_transient_failure(self, monkeypatch):
        adapter = _adapter()
        calls = []

        def fake_api_call(method, payload, timeout=10):
            calls.append((method, payload, timeout))
            if len(calls) == 1:
                raise RuntimeError("transient")
            return {
                "ok": True,
                "result": [
                    {"update_id": 220, "message": {"text": "latest"}},
                ],
            }

        monkeypatch.setattr(adapter, "_api_call", fake_api_call)

        adapter._sync_startup_offset()
        assert adapter._offset is None
        assert adapter._startup_synced is False

        adapter._sync_startup_offset()

        assert adapter._offset == 221
        assert adapter._startup_synced is True

    def test_immediate_duplicate_poll_error_after_startup_sync_failure_is_suppressed(self, monkeypatch, capsys):
        adapter = _adapter()

        adapter._log_poll_error(RuntimeError("Telegram API getUpdates network error: dns"), source="startup_sync")
        adapter._log_poll_error(RuntimeError("Telegram API getUpdates network error: dns"), source="poll")

        err = capsys.readouterr().err
        assert err.count("Telegram API getUpdates network error: dns") == 1

    def test_distinct_poll_error_is_still_logged_after_startup_sync_failure(self, monkeypatch, capsys):
        adapter = _adapter()

        adapter._log_poll_error(RuntimeError("Telegram API getUpdates network error: dns"), source="startup_sync")
        adapter._log_poll_error(RuntimeError("different failure"), source="poll")

        err = capsys.readouterr().err
        assert "Startup sync skipped" in err
        assert "Poll error" in err

    def test_news_command_uses_news_backend(self, monkeypatch):
        adapter = _adapter()
        sent = []
        saved = []

        monkeypatch.setattr("archon.adapters.telegram.ensure_dirs", lambda: None)
        monkeypatch.setattr("archon.adapters.telegram.load_config", lambda: Config())
        monkeypatch.setattr("archon.adapters.telegram.new_session_id", lambda: "20260225-070001")
        monkeypatch.setattr(
            "archon.adapters.telegram.save_exchange",
            lambda session_id, user_msg, assistant_msg: saved.append((session_id, user_msg, assistant_msg)),
        )
        monkeypatch.setattr(adapter, "_send_typing", lambda chat_id: None)
        monkeypatch.setattr(
            "archon.adapters.telegram.get_or_build_news_digest",
            lambda _cfg, force_refresh=False: NewsRunResult(
                status="preview",
                reason="cache_hit",
                digest=NewsDigest(
                    date_iso="2026-02-24",
                    markdown="Digest markdown",
                    used_fallback=False,
                    item_count=4,
                    items=[],
                ),
            ),
        )
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]

        adapter._handle_message(
            {
                "text": "/news",
                "chat": {"id": 99},
                "from": {"id": 42},
            }
        )

        assert sent
        assert sent[0][0] == 99
        assert "Digest markdown" in sent[0][1]
        assert "cached digest" in sent[0][1]
        assert saved
        assert saved[0][0] == "tg-99-20260225-070001"
        assert saved[0][1] == "/news"
        assert "Digest markdown" in saved[0][2]

    def test_news_request_text_uses_news_backend(self, monkeypatch):
        adapter = TelegramAdapter(
            token="123:abc",
            allowed_user_ids=[42],
            agent_factory=lambda: (_ for _ in ()).throw(RuntimeError("agent should not run")),
            poll_timeout_sec=1,
        )
        sent = []
        saved = []

        monkeypatch.setattr("archon.adapters.telegram.ensure_dirs", lambda: None)
        monkeypatch.setattr("archon.adapters.telegram.load_config", lambda: Config())
        monkeypatch.setattr("archon.adapters.telegram.new_session_id", lambda: "20260225-070002")
        monkeypatch.setattr(
            "archon.adapters.telegram.save_exchange",
            lambda session_id, user_msg, assistant_msg: saved.append((session_id, user_msg, assistant_msg)),
        )
        monkeypatch.setattr(adapter, "_send_typing", lambda chat_id: None)
        monkeypatch.setattr(
            "archon.adapters.telegram.get_or_build_news_digest",
            lambda _cfg, force_refresh=False: NewsRunResult(
                status="preview",
                reason="cache_hit",
                digest=NewsDigest(
                    date_iso="2026-02-24",
                    markdown="Digest markdown",
                    used_fallback=False,
                    item_count=4,
                    items=[],
                ),
            ),
        )
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]

        adapter._handle_message(
            {
                "text": "Could you send me today's AI news briefing?",
                "chat": {"id": 99},
                "from": {"id": 42},
            }
        )

        assert sent
        assert sent[0][0] == 99
        assert "Digest markdown" in sent[0][1]
        assert saved
        assert saved[0][0] == "tg-99-20260225-070002"
        assert saved[0][1] == "Could you send me today's AI news briefing?"
        assert "Digest markdown" in saved[0][2]

    def test_news_request_voice_uses_news_backend(self, monkeypatch):
        adapter = TelegramAdapter(
            token="123:abc",
            allowed_user_ids=[42],
            agent_factory=lambda: (_ for _ in ()).throw(RuntimeError("agent should not run")),
            poll_timeout_sec=1,
        )
        sent = []
        sent_voices = []
        saved = []
        events = []

        monkeypatch.setattr("archon.adapters.telegram.ensure_dirs", lambda: None)
        monkeypatch.setattr("archon.adapters.telegram.load_config", lambda: Config())
        monkeypatch.setattr("archon.adapters.telegram.new_session_id", lambda: "20260225-070003")
        monkeypatch.setattr(
            "archon.adapters.telegram.save_exchange",
            lambda session_id, user_msg, assistant_msg: saved.append((session_id, user_msg, assistant_msg)),
        )
        monkeypatch.setattr(adapter, "_send_typing", lambda chat_id: None)
        monkeypatch.setattr(adapter._bot, "get_file", lambda file_id, timeout=10: {"file_path": "voice/a.ogg"})
        monkeypatch.setattr(adapter._bot, "download_file", lambda file_path, timeout=20: b"audio-data")
        monkeypatch.setattr(
            "archon.adapters.telegram.transcribe_audio_bytes",
            lambda data, mime_type: "can you send me today's AI news now",
        )
        monkeypatch.setattr(
            "archon.adapters.telegram.synthesize_speech_wav",
            lambda text: (b"RIFF....WAVE", "audio/wav"),
        )
        monkeypatch.setattr(
            "archon.adapters.telegram.convert_wav_to_ogg_opus",
            lambda data: (b"OggS....", "audio/ogg"),
        )
        monkeypatch.setattr(
            adapter._bot,
            "send_voice_bytes",
            lambda chat_id, filename, data, **kwargs: sent_voices.append((chat_id, filename, data, kwargs))
            or {"message_id": 901},
        )
        monkeypatch.setattr(
            "archon.adapters.telegram.get_or_build_news_digest",
            lambda _cfg, force_refresh=False: NewsRunResult(
                status="preview",
                reason="cache_hit",
                digest=NewsDigest(
                    date_iso="2026-02-24",
                    markdown="Digest markdown",
                    used_fallback=False,
                    item_count=4,
                    items=[],
                ),
            ),
        )
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]
        adapter.set_activity_sink(lambda event: events.append((event.source, event.message)))

        adapter._handle_message(
            {
                "voice": {"file_id": "f1", "mime_type": "audio/ogg"},
                "chat": {"id": 99},
                "from": {"id": 42},
            }
        )

        assert sent
        assert sent[0][0] == 99
        assert "Digest markdown" in sent[0][1]
        assert sent_voices
        assert sent_voices[0][0] == 99
        assert any("voice reply sent to 99" in event for _, event in events)
        assert saved
        assert saved[0][0] == "tg-99-20260225-070003"
        assert saved[0][1] == "[voice] can you send me today's AI news now"
        assert "Digest markdown" in saved[0][2]

    def test_news_request_voice_phrase_from_history_uses_news_backend(self, monkeypatch):
        adapter = TelegramAdapter(
            token="123:abc",
            allowed_user_ids=[42],
            agent_factory=lambda: (_ for _ in ()).throw(RuntimeError("agent should not run")),
            poll_timeout_sec=1,
        )
        sent = []
        saved = []

        monkeypatch.setattr("archon.adapters.telegram.ensure_dirs", lambda: None)
        monkeypatch.setattr("archon.adapters.telegram.load_config", lambda: Config())
        monkeypatch.setattr("archon.adapters.telegram.new_session_id", lambda: "20260310-155216")
        monkeypatch.setattr(
            "archon.adapters.telegram.save_exchange",
            lambda session_id, user_msg, assistant_msg: saved.append((session_id, user_msg, assistant_msg)),
        )
        monkeypatch.setattr(adapter, "_send_typing", lambda chat_id: None)
        monkeypatch.setattr(adapter._bot, "get_file", lambda file_id, timeout=10: {"file_path": "voice/a.ogg"})
        monkeypatch.setattr(adapter._bot, "download_file", lambda file_path, timeout=20: b"audio-data")
        monkeypatch.setattr(
            "archon.adapters.telegram.transcribe_audio_bytes",
            lambda data, mime_type: "Simula AI news today",
        )
        monkeypatch.setattr(
            "archon.adapters.telegram.synthesize_speech_wav",
            lambda text: (b"RIFF....WAVE", "audio/wav"),
        )
        monkeypatch.setattr(
            "archon.adapters.telegram.convert_wav_to_ogg_opus",
            lambda data: (b"OggS....", "audio/ogg"),
        )
        monkeypatch.setattr(
            adapter._bot,
            "send_voice_bytes",
            lambda chat_id, filename, data, **kwargs: {"message_id": 902},
        )
        monkeypatch.setattr(
            "archon.adapters.telegram.get_or_build_news_digest",
            lambda _cfg, force_refresh=False: NewsRunResult(
                status="preview",
                reason="cache_hit",
                digest=NewsDigest(
                    date_iso="2026-03-10",
                    markdown="Digest markdown",
                    used_fallback=False,
                    item_count=4,
                    items=[],
                ),
            ),
        )
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]

        adapter._handle_message(
            {
                "voice": {"file_id": "f1", "mime_type": "audio/ogg"},
                "chat": {"id": 99},
                "from": {"id": 42},
            }
        )

        assert sent
        assert "Digest markdown" in sent[0][1]
        assert saved
        assert saved[0][0] == "tg-99-20260310-155216"
        assert saved[0][1] == "[voice] Simula AI news today"
        assert "Digest markdown" in saved[0][2]

    def test_news_status_command_reports_cache_hit(self, monkeypatch):
        adapter = _adapter()
        sent = []

        monkeypatch.setattr("archon.adapters.telegram.ensure_dirs", lambda: None)
        monkeypatch.setattr(
            "archon.adapters.telegram.load_news_state",
            lambda: {"last_run": "2026-02-24", "status": "success", "timestamp": 1.0},
        )
        monkeypatch.setattr(
            "archon.adapters.telegram.load_cached_digest",
            lambda date_iso=None: NewsDigest(
                date_iso=date_iso or "2026-02-24",
                markdown="Digest",
                used_fallback=True,
                item_count=2,
                items=[],
            ),
        )
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]

        adapter._handle_message(
            {
                "text": "/news_status",
                "chat": {"id": 99},
                "from": {"id": 42},
            }
        )

        assert sent
        assert "today_cache: hit" in sent[0][1]
        assert "cache_meta: items=2" in sent[0][1]

    def test_job_command_renders_job_summary(self, monkeypatch):
        adapter = _adapter()
        sent = []
        saved = []

        monkeypatch.setattr("archon.adapters.telegram.new_session_id", lambda: "20260225-090000")
        monkeypatch.setattr(
            "archon.adapters.telegram.save_exchange",
            lambda session_id, user_msg, assistant_msg: saved.append((session_id, user_msg, assistant_msg)),
        )
        monkeypatch.setattr(
            "archon.adapters.telegram.handle_job_command",
            lambda agent, text: (
                True,
                "job_id: worker:sess-1\n"
                "job_kind: worker_session\n"
                "job_status: ok\n"
                "job_summary: Looks good\n"
                "job_last_update_at: 2026-02-24T00:00:10Z",
            ),
        )
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]

        adapter._handle_message(
            {
                "text": "/job worker:sess-1",
                "chat": {"id": 99},
                "from": {"id": 42},
            }
        )

        assert sent
        assert "job_id: worker:sess-1" in sent[0][1]
        assert "job_kind: worker_session" in sent[0][1]
        assert saved
        assert saved[0][0] == "tg-99-20260225-090000"
        assert saved[0][1] == "/job worker:sess-1"

    def test_natural_language_research_status_uses_local_job_store_without_model_turn(self, monkeypatch):
        adapter = TelegramAdapter(
            token="123:abc",
            allowed_user_ids=[42],
            agent_factory=lambda: _TelegramLocalCommandAgent(),
            poll_timeout_sec=1,
        )
        sent = []
        saved = []

        monkeypatch.setattr("archon.adapters.telegram.new_session_id", lambda: "20260311-090000")
        monkeypatch.setattr(
            "archon.adapters.telegram.save_exchange",
            lambda session_id, user_msg, assistant_msg: saved.append((session_id, user_msg, assistant_msg)),
        )
        monkeypatch.setattr(
            "archon.adapters.telegram.handle_jobs_command",
            lambda agent, text: (
                True,
                "job_id: research:v1_abc\njob_kind: deep_research\njob_status: completed",
            ) if text == "/jobs show research:v1_abc" else (False, ""),
        )
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]

        adapter._handle_message(
            {
                "text": "show me the status of research:v1_abc",
                "chat": {"id": 99},
                "from": {"id": 42},
            }
        )

        assert sent
        assert "job_id: research:v1_abc" in sent[0][1]
        assert saved
        assert saved[0][0] == "tg-99-20260311-090000"
        assert saved[0][1] == "show me the status of research:v1_abc"
        assert adapter._agents[99].run_calls == []  # type: ignore[attr-defined]

    def test_natural_language_active_jobs_uses_local_job_store_without_model_turn(self, monkeypatch):
        adapter = TelegramAdapter(
            token="123:abc",
            allowed_user_ids=[42],
            agent_factory=lambda: _TelegramLocalCommandAgent(),
            poll_timeout_sec=1,
        )
        sent = []
        saved = []

        monkeypatch.setattr("archon.adapters.telegram.new_session_id", lambda: "20260311-090001")
        monkeypatch.setattr(
            "archon.adapters.telegram.save_exchange",
            lambda session_id, user_msg, assistant_msg: saved.append((session_id, user_msg, assistant_msg)),
        )
        monkeypatch.setattr(
            "archon.adapters.telegram.handle_jobs_command",
            lambda agent, text: (
                True,
                "Jobs:\n- research:v1_abc [deep_research] running | now | Research in progress",
            ) if text == "/jobs active" else (False, ""),
        )
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]

        adapter._handle_message(
            {
                "text": "show me active jobs",
                "chat": {"id": 99},
                "from": {"id": 42},
            }
        )

        assert sent
        assert sent[0][1].startswith("Jobs:")
        assert saved
        assert saved[0][0] == "tg-99-20260311-090001"
        assert saved[0][1] == "show me active jobs"
        assert adapter._agents[99].run_calls == []  # type: ignore[attr-defined]

    def test_jobs_command_renders_job_list(self, monkeypatch):
        adapter = _adapter()
        sent = []
        saved = []

        monkeypatch.setattr("archon.adapters.telegram.new_session_id", lambda: "20260225-090001")
        monkeypatch.setattr(
            "archon.adapters.telegram.save_exchange",
            lambda session_id, user_msg, assistant_msg: saved.append((session_id, user_msg, assistant_msg)),
        )
        monkeypatch.setattr(
            "archon.adapters.telegram.handle_jobs_command",
            lambda agent, text: (
                True,
                "Jobs:\n"
                "- worker:sess-1 [worker_session] ok | 2026-02-24T00:00:10Z | Looks good\n"
                "- call:call-1 [call_mission] queued | 2026-02-24T00:00:09Z | Call me",
            ),
        )
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]

        adapter._handle_message(
            {
                "text": "/jobs",
                "chat": {"id": 99},
                "from": {"id": 42},
            }
        )

        assert sent
        assert "worker:sess-1" in sent[0][1]
        assert "call:call-1" in sent[0][1]
        assert saved
        assert saved[0][0] == "tg-99-20260225-090001"
        assert saved[0][1] == "/jobs"

    def test_jobs_command_does_not_require_agent_creation(self, monkeypatch):
        adapter = TelegramAdapter(
            token="123:abc",
            allowed_user_ids=[42],
            agent_factory=lambda: (_ for _ in ()).throw(RuntimeError("llm init failed")),
            poll_timeout_sec=1,
        )
        sent = []

        monkeypatch.setattr("archon.adapters.telegram.new_session_id", lambda: "20260306-180000")
        monkeypatch.setattr(
            "archon.adapters.telegram.save_exchange",
            lambda session_id, user_msg, assistant_msg: None,
        )
        monkeypatch.setattr(
            "archon.adapters.telegram.handle_jobs_command",
            lambda agent, text: (
                True,
                "Jobs:\n- worker:sess-1 [worker_session] ok | 2026-02-24T00:00:10Z | Looks good",
            ),
        )
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]

        adapter._handle_message(
            {
                "text": "/jobs",
                "chat": {"id": 99},
                "from": {"id": 42},
            }
        )

        assert sent
        assert "worker:sess-1" in sent[0][1]

    def test_doctor_command_degrades_gracefully_when_agent_creation_fails(self, monkeypatch):
        adapter = TelegramAdapter(
            token="123:abc",
            allowed_user_ids=[42],
            agent_factory=lambda: (_ for _ in ()).throw(RuntimeError("llm init failed")),
            poll_timeout_sec=1,
        )
        sent = []
        cfg = Config()
        cfg.llm.provider = "openai"
        cfg.llm.model = "gpt-5-mini"
        cfg.llm.api_key = ""

        monkeypatch.setattr("archon.adapters.telegram.new_session_id", lambda: "20260306-180001")
        monkeypatch.setattr(
            "archon.adapters.telegram.save_exchange",
            lambda session_id, user_msg, assistant_msg: None,
        )
        monkeypatch.setattr("archon.adapters.telegram.load_config", lambda: cfg)
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]

        adapter._handle_message(
            {
                "text": "/doctor",
                "chat": {"id": 99},
                "from": {"id": 42},
            }
        )

        assert sent
        assert sent[0][1].startswith("Degraded mode: live chat agent unavailable")
        assert "Doctor:" in sent[0][1]
        assert "llm=" in sent[0][1]

    def test_status_command_labels_fallback_as_local_snapshot(self, monkeypatch):
        adapter = TelegramAdapter(
            token="123:abc",
            allowed_user_ids=[42],
            agent_factory=lambda: (_ for _ in ()).throw(RuntimeError("llm init failed")),
            poll_timeout_sec=1,
        )
        sent = []
        cfg = Config()
        cfg.llm.provider = "openai"
        cfg.llm.model = "gpt-5-mini"

        monkeypatch.setattr("archon.adapters.telegram.new_session_id", lambda: "20260307-010001")
        monkeypatch.setattr(
            "archon.adapters.telegram.save_exchange",
            lambda session_id, user_msg, assistant_msg: None,
        )
        monkeypatch.setattr("archon.adapters.telegram.load_config", lambda: cfg)
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]

        adapter._handle_message(
            {
                "text": "/status",
                "chat": {"id": 99},
                "from": {"id": 42},
            }
        )

        assert sent
        assert sent[0][1].startswith("Degraded mode: live chat agent unavailable")
        assert "using local fallback snapshot" in sent[0][1]
        assert "Status:" in sent[0][1]

    def test_status_command_prefixes_transport_health_when_degraded(self, monkeypatch):
        adapter = TelegramAdapter(
            token="123:abc",
            allowed_user_ids=[42],
            agent_factory=lambda: _TelegramLocalCommandAgent(),
            poll_timeout_sec=1,
        )
        sent = []

        monkeypatch.setattr("archon.adapters.telegram.new_session_id", lambda: "20260307-010001")
        monkeypatch.setattr(
            "archon.adapters.telegram.save_exchange",
            lambda session_id, user_msg, assistant_msg: None,
        )
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]
        adapter._set_transport_health("degraded", error=RuntimeError("dns"), source="poll")

        adapter._handle_message(
            {
                "text": "/status",
                "chat": {"id": 99},
                "from": {"id": 42},
            }
        )

        assert sent
        assert sent[0][1].startswith("Telegram transport: degraded")
        assert "Status:" in sent[0][1]

    def test_profile_fallback_uses_configured_default_profile(self, monkeypatch):
        adapter = TelegramAdapter(
            token="123:abc",
            allowed_user_ids=[42],
            agent_factory=lambda: (_ for _ in ()).throw(RuntimeError("llm init failed")),
            poll_timeout_sec=1,
        )
        sent = []
        cfg = Config()
        cfg.orchestrator.enabled = True
        cfg.orchestrator.default_profile = "safe"
        cfg.profiles = {
            "default": ProfileConfig(),
            "safe": ProfileConfig(allowed_tools=["memory_read"], max_mode="review"),
        }

        monkeypatch.setattr("archon.adapters.telegram.new_session_id", lambda: "20260307-010003")
        monkeypatch.setattr(
            "archon.adapters.telegram.save_exchange",
            lambda session_id, user_msg, assistant_msg: None,
        )
        monkeypatch.setattr("archon.adapters.telegram.load_config", lambda: cfg)
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]

        adapter._handle_message(
            {
                "text": "/profile",
                "chat": {"id": 99},
                "from": {"id": 42},
            }
        )

        assert sent
        assert sent[0][1].startswith("Degraded mode: live chat agent unavailable")
        assert "Policy profile: safe" in sent[0][1]

    def test_jobs_fallback_is_labeled_as_local_job_store(self, monkeypatch):
        adapter = TelegramAdapter(
            token="123:abc",
            allowed_user_ids=[42],
            agent_factory=lambda: (_ for _ in ()).throw(RuntimeError("llm init failed")),
            poll_timeout_sec=1,
        )
        sent = []
        cfg = Config()

        monkeypatch.setattr("archon.adapters.telegram.new_session_id", lambda: "20260307-010004")
        monkeypatch.setattr(
            "archon.adapters.telegram.save_exchange",
            lambda session_id, user_msg, assistant_msg: None,
        )
        monkeypatch.setattr("archon.adapters.telegram.load_config", lambda: cfg)
        monkeypatch.setattr(
            "archon.adapters.telegram.handle_jobs_command",
            lambda agent, text: (True, "Jobs: none"),
        )
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]

        adapter._handle_message(
            {
                "text": "/jobs",
                "chat": {"id": 99},
                "from": {"id": 42},
            }
        )

        assert sent
        assert sent[0][1].startswith("Degraded mode: live chat agent unavailable")
        assert "using local job store" in sent[0][1]
        assert sent[0][1].endswith("Jobs: none")

    def test_job_fallback_is_labeled_as_local_job_store(self, monkeypatch):
        adapter = TelegramAdapter(
            token="123:abc",
            allowed_user_ids=[42],
            agent_factory=lambda: (_ for _ in ()).throw(RuntimeError("llm init failed")),
            poll_timeout_sec=1,
        )
        sent = []
        cfg = Config()

        monkeypatch.setattr("archon.adapters.telegram.new_session_id", lambda: "20260307-010004")
        monkeypatch.setattr(
            "archon.adapters.telegram.save_exchange",
            lambda session_id, user_msg, assistant_msg: None,
        )
        monkeypatch.setattr("archon.adapters.telegram.load_config", lambda: cfg)
        monkeypatch.setattr(
            "archon.adapters.telegram.handle_job_command",
            lambda agent, text: (True, "job_id: worker:sess-1"),
        )
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]

        adapter._handle_message(
            {
                "text": "/job worker:sess-1",
                "chat": {"id": 99},
                "from": {"id": 42},
            }
        )

        assert sent
        assert sent[0][1].startswith("Degraded mode: live chat agent unavailable")
        assert "using local job store" in sent[0][1]
        assert sent[0][1].endswith("job_id: worker:sess-1")

    def test_job_cancel_unavailable_when_live_agent_fails_to_start(self, monkeypatch):
        adapter = TelegramAdapter(
            token="123:abc",
            allowed_user_ids=[42],
            agent_factory=lambda: (_ for _ in ()).throw(RuntimeError("llm init failed")),
            poll_timeout_sec=1,
        )
        sent = []

        monkeypatch.setattr("archon.adapters.telegram.new_session_id", lambda: "20260307-010005")
        monkeypatch.setattr(
            "archon.adapters.telegram.save_exchange",
            lambda session_id, user_msg, assistant_msg: None,
        )
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]

        adapter._handle_message(
            {
                "text": "/job cancel research:abc",
                "chat": {"id": 99},
                "from": {"id": 42},
            }
        )

        assert sent
        assert sent[0][1] == "Local command unavailable: live chat agent failed to start."

    def test_jobs_purge_unavailable_when_live_agent_fails_to_start(self, monkeypatch):
        adapter = TelegramAdapter(
            token="123:abc",
            allowed_user_ids=[42],
            agent_factory=lambda: (_ for _ in ()).throw(RuntimeError("llm init failed")),
            poll_timeout_sec=1,
        )
        sent = []

        monkeypatch.setattr("archon.adapters.telegram.new_session_id", lambda: "20260307-010006")
        monkeypatch.setattr(
            "archon.adapters.telegram.save_exchange",
            lambda session_id, user_msg, assistant_msg: None,
        )
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]

        adapter._handle_message(
            {
                "text": "/jobs purge",
                "chat": {"id": 99},
                "from": {"id": 42},
            }
        )

        assert sent
        assert sent[0][1] == "Local command unavailable: live chat agent failed to start."

    def test_plain_chat_agent_creation_failure_reports_real_error(self, monkeypatch):
        adapter = TelegramAdapter(
            token="123:abc",
            allowed_user_ids=[42],
            agent_factory=lambda: (_ for _ in ()).throw(RuntimeError("llm init failed")),
            poll_timeout_sec=1,
        )
        sent = []
        saved = []

        monkeypatch.setattr("archon.adapters.telegram.new_session_id", lambda: "20260307-010002")
        monkeypatch.setattr(
            "archon.adapters.telegram.save_exchange",
            lambda session_id, user_msg, assistant_msg: saved.append((session_id, user_msg, assistant_msg)),
        )
        monkeypatch.setattr(adapter, "_send_typing", lambda chat_id: None)
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]

        adapter._handle_message(
            {
                "text": "hello",
                "chat": {"id": 99},
                "from": {"id": 42},
            }
        )

        assert sent
        assert sent[0][1] == "Error: RuntimeError: llm init failed"
        assert saved[-1] == ("tg-99-20260307-010002", "hello", "Error: RuntimeError: llm init failed")

    def test_sync_bot_commands_replaces_remote_command_menu(self, monkeypatch):
        adapter = _adapter()
        calls = []

        monkeypatch.setattr(
            adapter,
            "_api_call",
            lambda method, payload, timeout: calls.append((method, payload, timeout)) or {"ok": True},
        )

        adapter._sync_bot_commands()

        assert calls == [
            (
                "setMyCommands",
                {
                    "commands": [
                        {"command": "start", "description": "Connect and show basics"},
                        {"command": "help", "description": "Show command guide"},
                        {"command": "status", "description": "Inspect session state"},
                        {"command": "new", "description": "Fresh chat context"},
                        {"command": "compact", "description": "Reduce context pressure"},
                        {"command": "context", "description": "Inspect context state"},
                        {"command": "cost", "description": "Show token usage"},
                        {"command": "jobs", "description": "List background jobs"},
                        {"command": "approvals", "description": "Inspect approval state"},
                        {"command": "skills", "description": "List available skills"},
                        {"command": "mcp", "description": "Inspect MCP servers"},
                        {"command": "reset", "description": "Reset chat session"},
                    ]
                },
                10,
            )
        ]

    def test_run_loop_syncs_bot_commands_once_on_startup(self, monkeypatch):
        adapter = _adapter()
        sync_calls = []

        monkeypatch.setattr(adapter, "_sync_bot_commands", lambda: sync_calls.append("sync"))
        monkeypatch.setattr(adapter, "_sync_startup_offset", lambda: None)
        monkeypatch.setattr(
            adapter,
            "_get_updates",
            lambda: adapter._stop_event.set() or [],
        )

        adapter._run_loop()

        assert sync_calls == ["sync"]

    def test_job_lane_route_progress_is_sent_before_final_reply(self, monkeypatch):
        adapter = TelegramAdapter(
            token="123:abc",
            allowed_user_ids=[42],
            agent_factory=lambda: _JobRouteAgent(),
            poll_timeout_sec=1,
        )
        sent = []
        saved = []

        monkeypatch.setattr("archon.adapters.telegram.new_session_id", lambda: "20260225-090002")
        monkeypatch.setattr(
            "archon.adapters.telegram.save_exchange",
            lambda session_id, user_msg, assistant_msg: saved.append((session_id, user_msg, assistant_msg)),
        )
        monkeypatch.setattr(adapter, "_send_typing", lambda chat_id: None)
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]

        adapter._handle_message(
            {
                "text": "research this deeply",
                "chat": {"id": 99},
                "from": {"id": 42},
            }
        )

        assert len(sent) == 2
        assert "route: job" in sent[0][1].lower()
        assert "broad scope request" in sent[0][1].lower()
        assert sent[1][1] == "ok"
        assert saved
        assert saved[0][0] == "tg-99-20260225-090002"
        assert saved[0][1] == "research this deeply"
        assert saved[0][2] == "ok"

    def test_approve_next_allows_one_dangerous_action(self, monkeypatch):
        adapter = _adapter()
        sent = []
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]

        # Dangerous action is blocked by default and gives Telegram guidance.
        allowed1 = adapter._confirm_for_chat(99, "pacman -Q | head", Level.DANGEROUS)
        assert allowed1 is False
        assert sent
        assert "approve_next" in sent[-1][1]

        adapter._handle_message(
            {
                "text": "/approve_next",
                "chat": {"id": 99},
                "from": {"id": 42},
            }
        )
        assert (
            sent[-1][1]
            == "Approval: result=allow_once_armed | dangerous_mode=off | pending_request=none | "
            "allow_once_remaining=1 | next=one_future_dangerous_action_allowed | review=/approvals"
        )

        # First dangerous action after /approve_next is allowed, next one is blocked again.
        allowed2 = adapter._confirm_for_chat(99, "pacman -Q | head", Level.DANGEROUS)
        allowed3 = adapter._confirm_for_chat(99, "pacman -Q | head", Level.DANGEROUS)
        assert allowed2 is True
        assert allowed3 is False

    def test_approvals_status_uses_same_structured_state_text(self, monkeypatch):
        adapter = _adapter()
        sent = []
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]

        adapter._handle_message(
            {
                "text": "/approvals",
                "chat": {"id": 99},
                "from": {"id": 42},
            }
        )

        assert sent[-1][1] == (
            "Approvals: dangerous_mode=off | pending_request=none | allow_once_remaining=0 | "
            "replay=/approve | allow_once=/approve_next | deny=/deny"
        )

    def test_approvals_on_off_toggles_dangerous_actions(self, monkeypatch):
        adapter = _adapter()
        sent = []
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]

        adapter._handle_message(
            {
                "text": "/approvals on",
                "chat": {"id": 99},
                "from": {"id": 42},
            }
        )
        assert adapter._confirm_for_chat(99, "pacman -Q | head", Level.DANGEROUS) is True
        assert (
            "Approvals: result=dangerous_mode_enabled | dangerous_mode=on | pending_request=none | "
            "allow_once_remaining=0 | replay=/approve | allow_once=/approve_next | deny=/deny"
        ) in [msg for _chat, msg in sent]

        adapter._handle_message(
            {
                "text": "/approvals off",
                "chat": {"id": 99},
                "from": {"id": 42},
            }
        )
        assert adapter._confirm_for_chat(99, "pacman -Q | head", Level.DANGEROUS) is False
        assert (
            "Approvals: result=dangerous_mode_disabled | dangerous_mode=off | pending_request=none | "
            "allow_once_remaining=0 | replay=/approve | allow_once=/approve_next | deny=/deny"
        ) in [msg for _chat, msg in sent]

    def test_blocked_dangerous_action_creates_pending_request_and_suppresses_duplicate_reply(self, monkeypatch):
        adapter = TelegramAdapter(
            token="123:abc",
            allowed_user_ids=[42],
            agent_factory=lambda: _DangerousAgent(),
            poll_timeout_sec=1,
        )
        sent = []
        monkeypatch.setattr(adapter, "_send_typing", lambda chat_id: None)
        monkeypatch.setattr(
            "archon.adapters.telegram.save_exchange",
            lambda session_id, user_msg, assistant_msg: None,
        )
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]
        monkeypatch.setattr(
            adapter._bot,
            "send_message",
            lambda chat_id, text, **kwargs: sent.append((chat_id, text)) or {"message_id": 501},
        )

        adapter._handle_message({"text": "check system packages", "chat": {"id": 99}, "from": {"id": 42}})

        # Smooth UX target: only the approval prompt is sent, not a second rejected-tool message.
        assert len(sent) == 1
        assert "Approve" in sent[0][1] or "blocked" in sent[0][1]
        pending = adapter._pending_approvals.get(99)  # type: ignore[attr-defined]
        assert pending is not None
        assert pending["user_text"] == "check system packages"

    def test_approve_command_replays_pending_request(self, monkeypatch):
        adapter = TelegramAdapter(
            token="123:abc",
            allowed_user_ids=[42],
            agent_factory=lambda: _DangerousAgent(),
            poll_timeout_sec=1,
        )
        sent = []
        monkeypatch.setattr(adapter, "_send_typing", lambda chat_id: None)
        monkeypatch.setattr(
            "archon.adapters.telegram.save_exchange",
            lambda session_id, user_msg, assistant_msg: None,
        )
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]
        monkeypatch.setattr(
            adapter._bot,
            "send_message",
            lambda chat_id, text, **kwargs: sent.append((chat_id, text)) or {"message_id": 502},
        )

        adapter._handle_message({"text": "check system packages", "chat": {"id": 99}, "from": {"id": 42}})
        adapter._handle_message({"text": "/approve", "chat": {"id": 99}, "from": {"id": 42}})

        assert (
            "Approval: result=approved_replaying | replayed_request=pacman -Q | head | dangerous_mode=off | "
            "pending_request=none | allow_once_remaining=0 | next=original_request_replayed_now | review=/approvals"
        ) in [msg for _chat, msg in sent]
        assert any("dangerous command output" in msg for _chat, msg in sent)
        pending = adapter._pending_approvals.get(99)  # type: ignore[attr-defined]
        assert pending is None or pending.get("status") in {"approved", "replayed", "cleared"}

    def test_deny_command_reports_cleared_pending_request(self, monkeypatch):
        adapter = TelegramAdapter(
            token="123:abc",
            allowed_user_ids=[42],
            agent_factory=lambda: _DangerousAgent(),
            poll_timeout_sec=1,
        )
        sent = []
        monkeypatch.setattr(adapter, "_send_typing", lambda chat_id: None)
        monkeypatch.setattr(
            "archon.adapters.telegram.save_exchange",
            lambda session_id, user_msg, assistant_msg: None,
        )
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]
        monkeypatch.setattr(
            adapter._bot,
            "send_message",
            lambda chat_id, text, **kwargs: sent.append((chat_id, text)) or {"message_id": 508},
        )

        adapter._handle_message({"text": "check system packages", "chat": {"id": 99}, "from": {"id": 42}})
        adapter._handle_message({"text": "/deny", "chat": {"id": 99}, "from": {"id": 42}})

        assert (
            "Approval: result=denied | denied_request=pacman -Q | head | dangerous_mode=off | "
            "pending_request=none | allow_once_remaining=0 | review=/approvals"
        ) in [msg for _chat, msg in sent]

    def test_new_blocked_request_replaces_pending_user_text_for_replay(self, monkeypatch):
        adapter = TelegramAdapter(
            token="123:abc",
            allowed_user_ids=[42],
            agent_factory=lambda: _DangerousAgent(),
            poll_timeout_sec=1,
        )
        sent = []
        monkeypatch.setattr(adapter, "_send_typing", lambda chat_id: None)
        monkeypatch.setattr(
            "archon.adapters.telegram.save_exchange",
            lambda session_id, user_msg, assistant_msg: None,
        )
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]
        monkeypatch.setattr(
            adapter._bot,
            "send_message",
            lambda chat_id, text, **kwargs: sent.append((chat_id, text)) or {"message_id": 504},
        )

        adapter._handle_message({"text": "first request", "chat": {"id": 99}, "from": {"id": 42}})
        adapter._handle_message({"text": "second request", "chat": {"id": 99}, "from": {"id": 42}})

        pending = adapter._pending_approvals.get(99)  # type: ignore[attr-defined]
        assert pending is not None
        assert pending["user_text"] == "second request"

        adapter._handle_message({"text": "/approve", "chat": {"id": 99}, "from": {"id": 42}})
        agent = adapter._agents[99]  # type: ignore[attr-defined]
        assert agent.messages[-1] == "second request"

    def test_callback_query_approve_answers_and_replays_pending_request(self, monkeypatch):
        adapter = TelegramAdapter(
            token="123:abc",
            allowed_user_ids=[42],
            agent_factory=lambda: _DangerousAgent(),
            poll_timeout_sec=1,
        )
        sent = []
        callbacks = []
        edits = []
        monkeypatch.setattr(adapter, "_send_typing", lambda chat_id: None)
        monkeypatch.setattr(
            "archon.adapters.telegram.save_exchange",
            lambda session_id, user_msg, assistant_msg: None,
        )
        adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]
        monkeypatch.setattr(
            adapter._bot,
            "send_message",
            lambda chat_id, text, **kwargs: sent.append((chat_id, text)) or {"message_id": 503},
        )
        monkeypatch.setattr(
            adapter._bot,
            "answer_callback_query",
            lambda callback_query_id, text=None, show_alert=False, timeout=5: callbacks.append(
                (callback_query_id, text, show_alert, timeout)
            ),
        )
        monkeypatch.setattr(
            adapter._bot,
            "edit_message_text",
            lambda chat_id, message_id, text, **kwargs: edits.append((chat_id, message_id, text, kwargs)),
        )

        adapter._handle_message({"text": "check system packages", "chat": {"id": 99}, "from": {"id": 42}})
        pending = adapter._pending_approvals.get(99)  # type: ignore[attr-defined]
        assert pending is not None
        pending["approval_message_id"] = 777
        approval_id = pending["approval_id"]

        adapter._process_update(
            {
                "callback_query": {
                    "id": "cb-1",
                    "from": {"id": 42},
                    "data": f"appr:{approval_id}:approve",
                    "message": {"message_id": 777, "chat": {"id": 99}},
                }
            }
        )

        assert callbacks and callbacks[0][0] == "cb-1"
        assert edits
        assert any("dangerous command output" in msg for _chat, msg in sent)


def test_help_groups_commands_by_operator_workflow():
    adapter = _adapter()
    sent = []
    adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]

    adapter._handle_message(
        {
            "text": "/help",
            "chat": {"id": 99},
            "from": {"id": 42},
        }
    )

    assert sent
    assert "Inspect state: /status, /context" in sent[0][1]
    assert "Reduce pressure: /compact, /new" in sent[0][1]
    assert "Handle blocked actions: /approvals, /approve, /approve_next, /deny" in sent[0][1]


def test_start_uses_same_workflow_help_headings():
    adapter = _adapter()
    sent = []
    adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]

    adapter._handle_message(
        {
            "text": "/start",
            "chat": {"id": 99},
            "from": {"id": 42},
        }
    )

    assert sent
    assert "Inspect state: /status, /context" in sent[0][1]
    assert "Reduce pressure: /compact, /new" in sent[0][1]
    assert "Handle blocked actions: /approvals, /approve, /approve_next, /deny" in sent[0][1]


def test_approve_blocked_dangerous_action_prompt_mentions_pending_request_and_replay_commands(monkeypatch):
    adapter = TelegramAdapter(
        token="123:abc",
        allowed_user_ids=[42],
        agent_factory=lambda: _DangerousAgent(),
        poll_timeout_sec=1,
    )
    sent = []
    monkeypatch.setattr(adapter, "_send_typing", lambda chat_id: None)
    monkeypatch.setattr(
        "archon.adapters.telegram.save_exchange",
        lambda session_id, user_msg, assistant_msg: None,
    )
    adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]
    monkeypatch.setattr(
        adapter._bot,
        "send_message",
        lambda chat_id, text, **kwargs: sent.append((chat_id, text)) or {"message_id": 506},
    )

    adapter._handle_message({"text": "check system packages", "chat": {"id": 99}, "from": {"id": 42}})

    assert len(sent) == 1
    assert "pending_request=pacman -Q | head" in sent[0][1]
    assert "replay=/approve" in sent[0][1]
    assert "replay_effect=replays_pending_request" in sent[0][1]
    assert "allow_once=/approve_next" in sent[0][1]
    assert "allow_once_effect=arms_one_future_dangerous_action" in sent[0][1]
    assert "review=/approvals" in sent[0][1]


def test_blocked_dangerous_action_without_user_text_uses_state_first_fallback():
    adapter = _adapter()
    sent = []
    adapter._send_text = lambda chat_id, text: sent.append((chat_id, text))  # type: ignore[method-assign]

    allowed = adapter._confirm_for_chat(99, "pacman -Q | head", Level.DANGEROUS)

    assert allowed is False
    assert sent
    assert "pending_request=pacman -Q | head" in sent[0][1]
    assert "replay=/approve" in sent[0][1]
    assert "replay_effect=replays_pending_request_when_original_message_is_available" in sent[0][1]
    assert "allow_once=/approve_next" in sent[0][1]
    assert "allow_once_effect=arms_one_future_dangerous_action" in sent[0][1]
    assert "state=original_request_missing" in sent[0][1]
    assert adapter._pending_approvals.get(99) is None  # type: ignore[attr-defined]


def test_chat_body_streams_final_reply_by_editing_one_message(monkeypatch):
    agent = _StreamingTelegramAgent(["Hello from telegram stream", " done"])
    adapter = TelegramAdapter(
        token="123:abc",
        allowed_user_ids=[42],
        agent_factory=lambda: agent,
        poll_timeout_sec=1,
    )
    sent_messages = []
    edited_messages = []
    fallback_sends = []
    saved = []

    monkeypatch.setattr(adapter, "_send_typing", lambda chat_id: None)
    monkeypatch.setattr(
        "archon.adapters.telegram.new_session_id",
        lambda: "20260323-140000",
    )
    monkeypatch.setattr(
        "archon.adapters.telegram.save_exchange",
        lambda session_id, user_msg, assistant_msg: saved.append((session_id, user_msg, assistant_msg)),
    )
    adapter._send_text = lambda chat_id, text: fallback_sends.append((chat_id, text))  # type: ignore[method-assign]
    monkeypatch.setattr(
        adapter._bot,
        "send_message",
        lambda chat_id, text, **kwargs: sent_messages.append((chat_id, text)) or {"message_id": 701},
    )
    monkeypatch.setattr(
        adapter._bot,
        "edit_message_text",
        lambda chat_id, message_id, text, **kwargs: edited_messages.append((chat_id, message_id, text)),
    )

    adapter._handle_message({"text": "hello", "chat": {"id": 99}, "from": {"id": 42}})

    assert agent.run_stream_calls == ["hello"]
    assert sent_messages == [(99, "Hello from telegram stream")]
    assert edited_messages == [(99, 701, "Hello from telegram stream done")]
    assert fallback_sends == []
    assert saved == [("tg-99-20260323-140000", "hello", "Hello from telegram stream done")]


def test_chat_body_streaming_edit_failure_falls_back_to_plain_send(monkeypatch):
    agent = _StreamingTelegramAgent(["Hello from telegram stream", " done"])
    adapter = TelegramAdapter(
        token="123:abc",
        allowed_user_ids=[42],
        agent_factory=lambda: agent,
        poll_timeout_sec=1,
    )
    sent_messages = []
    fallback_sends = []
    saved = []

    monkeypatch.setattr(adapter, "_send_typing", lambda chat_id: None)
    monkeypatch.setattr(
        "archon.adapters.telegram.new_session_id",
        lambda: "20260323-140100",
    )
    monkeypatch.setattr(
        "archon.adapters.telegram.save_exchange",
        lambda session_id, user_msg, assistant_msg: saved.append((session_id, user_msg, assistant_msg)),
    )
    adapter._send_text = lambda chat_id, text: fallback_sends.append((chat_id, text))  # type: ignore[method-assign]
    monkeypatch.setattr(
        adapter._bot,
        "send_message",
        lambda chat_id, text, **kwargs: sent_messages.append((chat_id, text)) or {"message_id": 702},
    )
    monkeypatch.setattr(
        adapter._bot,
        "edit_message_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("edit failed")),
    )

    adapter._handle_message({"text": "hello", "chat": {"id": 99}, "from": {"id": 42}})

    assert agent.run_stream_calls == ["hello"]
    assert sent_messages == [(99, "Hello from telegram stream")]
    assert fallback_sends == [(99, "Hello from telegram stream done")]
    assert saved == [("tg-99-20260323-140100", "hello", "Hello from telegram stream done")]


def test_chat_body_stream_no_chunk_does_not_rerun_turn(monkeypatch):
    class _NoChunkAgent:
        def __init__(self):
            self.hooks = HookBus()
            self.config = Config()
            self.config.llm.provider = "openai"
            self.config.llm.model = "gpt-5-mini"
            self.config.llm.api_key = "test-key"
            self.policy_profile = "default"
            self.session_id = ""
            self.total_input_tokens = 0
            self.total_output_tokens = 0
            self.history = [
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "stale previous reply"}],
                }
            ]
            self.log_label = ""
            self.on_thinking = None
            self.on_tool_call = None

        def run_stream(self, text):
            assert text == "hello"
            self.total_input_tokens += 7
            self.total_output_tokens += 2
            self.history.append(
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "buffered reply"}],
                }
            )
            if False:
                yield text

        def run(self, text):
            raise AssertionError(f"run() should not be called after no-chunk stream: {text}")

        def reset(self):
            return None

    adapter = TelegramAdapter(
        token="123:abc",
        allowed_user_ids=[42],
        agent_factory=lambda: _NoChunkAgent(),
        poll_timeout_sec=1,
    )
    sent_messages = []
    saved = []

    monkeypatch.setattr(adapter, "_send_typing", lambda chat_id: None)
    monkeypatch.setattr(
        "archon.adapters.telegram.new_session_id",
        lambda: "20260323-140200",
    )
    monkeypatch.setattr(
        "archon.adapters.telegram.save_exchange",
        lambda session_id, user_msg, assistant_msg: saved.append((session_id, user_msg, assistant_msg)),
    )
    monkeypatch.setattr(
        adapter._bot,
        "send_message",
        lambda chat_id, text, **kwargs: sent_messages.append((chat_id, text)) or {"message_id": 703},
    )

    adapter._handle_message({"text": "hello", "chat": {"id": 99}, "from": {"id": 42}})

    assert len(sent_messages) == 1
    assert sent_messages[0][1] == "buffered reply"
    assert sent_messages[0][1] != "stale previous reply"
    assert saved == [("tg-99-20260323-140200", "hello", "buffered reply")]


def test_chat_body_stream_no_chunk_does_not_reuse_stale_assistant_history(monkeypatch):
    class _NoChunkAgent:
        def __init__(self):
            self.hooks = HookBus()
            self.config = Config()
            self.config.llm.provider = "openai"
            self.config.llm.model = "gpt-5-mini"
            self.config.llm.api_key = "test-key"
            self.policy_profile = "default"
            self.session_id = ""
            self.total_input_tokens = 0
            self.total_output_tokens = 0
            self.history = [
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "stale previous reply"}],
                }
            ]
            self.log_label = ""
            self.on_thinking = None
            self.on_tool_call = None

        def run_stream(self, text):
            assert text == "hello"
            self.total_input_tokens += 7
            self.total_output_tokens += 0
            if False:
                yield text

        def run(self, text):
            raise AssertionError(f"run() should not be called after no-chunk stream: {text}")

        def reset(self):
            return None

    adapter = TelegramAdapter(
        token="123:abc",
        allowed_user_ids=[42],
        agent_factory=lambda: _NoChunkAgent(),
        poll_timeout_sec=1,
    )
    sent_messages = []
    saved = []

    monkeypatch.setattr(adapter, "_send_typing", lambda chat_id: None)
    monkeypatch.setattr(
        "archon.adapters.telegram.new_session_id",
        lambda: "20260323-140201",
    )
    monkeypatch.setattr(
        "archon.adapters.telegram.save_exchange",
        lambda session_id, user_msg, assistant_msg: saved.append((session_id, user_msg, assistant_msg)),
    )
    monkeypatch.setattr(
        adapter._bot,
        "send_message",
        lambda chat_id, text, **kwargs: sent_messages.append((chat_id, text)) or {"message_id": 704},
    )

    adapter._handle_message({"text": "hello", "chat": {"id": 99}, "from": {"id": 42}})

    assert len(sent_messages) == 1
    assert sent_messages[0][1] == "(empty response)"
    assert sent_messages[0][1] != "stale previous reply"
    assert saved == [("tg-99-20260323-140201", "hello", "(empty response)")]


def test_streaming_blocked_dangerous_action_suppresses_streamed_rejection(monkeypatch):
    adapter = TelegramAdapter(
        token="123:abc",
        allowed_user_ids=[42],
        agent_factory=lambda: _StreamingDangerousAgent(),
        poll_timeout_sec=1,
    )
    sent = []
    edits = []
    fallback_sends = []

    monkeypatch.setattr(adapter, "_send_typing", lambda chat_id: None)
    monkeypatch.setattr(
        "archon.adapters.telegram.save_exchange",
        lambda session_id, user_msg, assistant_msg: None,
    )
    adapter._send_text = lambda chat_id, text: fallback_sends.append((chat_id, text))  # type: ignore[method-assign]
    monkeypatch.setattr(
        adapter._bot,
        "send_message",
        lambda chat_id, text, **kwargs: sent.append((chat_id, text)) or {"message_id": 703},
    )
    monkeypatch.setattr(
        adapter._bot,
        "edit_message_text",
        lambda chat_id, message_id, text, **kwargs: edits.append((chat_id, message_id, text)),
    )

    adapter._handle_message({"text": "check system packages", "chat": {"id": 99}, "from": {"id": 42}})

    assert len(sent) == 1
    assert "pending_request=pacman -Q | head" in sent[0][1]
    assert fallback_sends == []
    assert edits == []
