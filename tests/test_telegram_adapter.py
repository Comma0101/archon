"""Tests for Telegram adapter command routing."""

from archon.adapters.telegram import TelegramAdapter
from archon.config import Config
from archon.news.models import NewsDigest, NewsRunResult
from archon.safety import Level


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


def _adapter():
    return TelegramAdapter(
        token="123:abc",
        allowed_user_ids=[42],
        agent_factory=lambda: _DummyAgent(),
        poll_timeout_sec=1,
    )


class TestTelegramAdapterCommands:
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

    def test_voice_message_tts_failure_does_not_break_text_reply(self, monkeypatch):
        adapter = _adapter()
        sent = []
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

        adapter._handle_message(
            {
                "voice": {"file_id": "f1", "mime_type": "audio/ogg"},
                "chat": {"id": 99},
                "from": {"id": 42},
            }
        )

        assert sent == [(99, "ok")]

    def test_voice_message_voice_upload_falls_back_to_wav_document(self, monkeypatch):
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

        # First dangerous action after /approve_next is allowed, next one is blocked again.
        allowed2 = adapter._confirm_for_chat(99, "pacman -Q | head", Level.DANGEROUS)
        allowed3 = adapter._confirm_for_chat(99, "pacman -Q | head", Level.DANGEROUS)
        assert allowed2 is True
        assert allowed3 is False

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
        assert any("enabled" in msg for _chat, msg in sent)

        adapter._handle_message(
            {
                "text": "/approvals off",
                "chat": {"id": 99},
                "from": {"id": 42},
            }
        )
        assert adapter._confirm_for_chat(99, "pacman -Q | head", Level.DANGEROUS) is False
        assert any("disabled" in msg for _chat, msg in sent)

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

        assert any("dangerous command output" in msg for _chat, msg in sent)
        pending = adapter._pending_approvals.get(99)  # type: ignore[attr-defined]
        assert pending is None or pending.get("status") in {"approved", "replayed", "cleared"}

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
