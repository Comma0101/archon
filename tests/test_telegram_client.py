"""Tests for shared Telegram Bot API client helpers."""

import json

from archon.adapters.telegram_client import TelegramBotClient, chunk_telegram_text


class _DummyResp:
    def __init__(self, payload: dict):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _RawResp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class TestTelegramClient:
    def test_chunk_telegram_text_splits_on_newlines(self):
        text = "a" * 10 + "\n" + "b" * 10
        chunks = chunk_telegram_text(text, limit=12)
        assert chunks == ["a" * 10, "b" * 10]

    def test_send_text_chunks_and_calls_send_message(self, monkeypatch):
        bot = TelegramBotClient("123:abc")
        calls = []

        monkeypatch.setattr(
            bot,
            "api_call",
            lambda method, payload, timeout=10: calls.append((method, payload, timeout)) or {"ok": True},
        )

        bot.send_text(99, "x" * 9 + "\n" + "y" * 9, limit=10, timeout=15)

        assert len(calls) == 2
        assert all(call[0] == "sendMessage" for call in calls)
        assert calls[0][1]["chat_id"] == 99
        assert calls[0][1]["disable_web_page_preview"] is True
        assert calls[0][2] == 15

    def test_send_text_includes_reply_markup_when_provided(self, monkeypatch):
        bot = TelegramBotClient("123:abc")
        calls = []

        monkeypatch.setattr(
            bot,
            "api_call",
            lambda method, payload, timeout=10: calls.append((method, payload, timeout)) or {"ok": True},
        )

        bot.send_text(99, "hi", reply_markup={"inline_keyboard": [[{"text": "OK", "callback_data": "x"}]]})

        assert calls[0][0] == "sendMessage"
        assert "reply_markup" in calls[0][1]
        assert calls[0][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "x"

    def test_answer_callback_query_calls_api(self, monkeypatch):
        bot = TelegramBotClient("123:abc")
        calls = []

        monkeypatch.setattr(
            bot,
            "api_call",
            lambda method, payload, timeout=10: calls.append((method, payload, timeout)) or {"ok": True},
        )

        bot.answer_callback_query("cb-1", text="Approved", show_alert=False)

        assert calls[0][0] == "answerCallbackQuery"
        assert calls[0][1]["callback_query_id"] == "cb-1"
        assert calls[0][1]["text"] == "Approved"

    def test_edit_message_text_calls_api(self, monkeypatch):
        bot = TelegramBotClient("123:abc")
        calls = []

        monkeypatch.setattr(
            bot,
            "api_call",
            lambda method, payload, timeout=10: calls.append((method, payload, timeout)) or {"ok": True},
        )

        bot.edit_message_text(
            99,
            123,
            "updated",
            reply_markup={"inline_keyboard": []},
        )

        assert calls[0][0] == "editMessageText"
        assert calls[0][1]["chat_id"] == 99
        assert calls[0][1]["message_id"] == 123
        assert calls[0][1]["text"] == "updated"
        assert calls[0][1]["reply_markup"] == {"inline_keyboard": []}

    def test_api_call_parses_json_success(self, monkeypatch):
        bot = TelegramBotClient("123:abc")

        monkeypatch.setattr(
            "archon.adapters.telegram_client.urlrequest.urlopen",
            lambda req, timeout=10: _DummyResp({"ok": True, "result": [{"update_id": 1}]}),
        )

        data = bot.api_call("getUpdates", {"timeout": 0}, timeout=5)
        assert data["ok"] is True
        assert data["result"][0]["update_id"] == 1

    def test_get_file_calls_api_and_returns_result(self, monkeypatch):
        bot = TelegramBotClient("123:abc")
        calls = []

        monkeypatch.setattr(
            bot,
            "api_call",
            lambda method, payload, timeout=10: calls.append((method, payload, timeout))
            or {"ok": True, "result": {"file_path": "voice/file.ogg"}},
        )

        result = bot.get_file("file-123")

        assert result == {"file_path": "voice/file.ogg"}
        assert calls == [("getFile", {"file_id": "file-123"}, 10)]

    def test_download_file_fetches_raw_bytes(self, monkeypatch):
        bot = TelegramBotClient("123:abc")
        seen = {}

        def fake_urlopen(req, timeout=10):
            seen["url"] = req.full_url
            seen["timeout"] = timeout
            return _RawResp(b"audio-bytes")

        monkeypatch.setattr("archon.adapters.telegram_client.urlrequest.urlopen", fake_urlopen)

        data = bot.download_file("voice/path.ogg", timeout=20)

        assert data == b"audio-bytes"
        assert seen["url"] == "https://api.telegram.org/file/bot123:abc/voice/path.ogg"
        assert seen["timeout"] == 20

    def test_send_document_bytes_uses_multipart_send_document(self, monkeypatch):
        bot = TelegramBotClient("123:abc")
        seen = {}

        def fake_urlopen(req, timeout=10):
            seen["url"] = req.full_url
            seen["timeout"] = timeout
            seen["content_type"] = req.headers.get("Content-type") or req.headers.get("Content-Type")
            seen["body"] = req.data
            return _DummyResp({"ok": True, "result": {"message_id": 77}})

        monkeypatch.setattr("archon.adapters.telegram_client.urlrequest.urlopen", fake_urlopen)

        result = bot.send_document_bytes(
            99,
            filename="reply.wav",
            data=b"RIFF....WAVE",
            caption="Voice reply",
            mime_type="audio/wav",
            timeout=18,
        )

        assert result["message_id"] == 77
        assert seen["url"] == "https://api.telegram.org/bot123:abc/sendDocument"
        assert seen["timeout"] == 18
        assert "multipart/form-data" in str(seen["content_type"])
        body = seen["body"]
        assert b'name="chat_id"' in body
        assert b"99" in body
        assert b'name="caption"' in body
        assert b"Voice reply" in body
        assert b'name="document"; filename="reply.wav"' in body
        assert b"RIFF....WAVE" in body

    def test_send_voice_bytes_uses_multipart_send_voice(self, monkeypatch):
        bot = TelegramBotClient("123:abc")
        seen = {}

        def fake_urlopen(req, timeout=10):
            seen["url"] = req.full_url
            seen["timeout"] = timeout
            seen["content_type"] = req.headers.get("Content-type") or req.headers.get("Content-Type")
            seen["body"] = req.data
            return _DummyResp({"ok": True, "result": {"message_id": 88}})

        monkeypatch.setattr("archon.adapters.telegram_client.urlrequest.urlopen", fake_urlopen)

        result = bot.send_voice_bytes(
            99,
            filename="reply.ogg",
            data=b"OggS....",
            caption="voice",
            mime_type="audio/ogg",
            timeout=16,
        )

        assert result["message_id"] == 88
        assert seen["url"] == "https://api.telegram.org/bot123:abc/sendVoice"
        assert seen["timeout"] == 16
        assert "multipart/form-data" in str(seen["content_type"])
        body = seen["body"]
        assert b'name="voice"; filename="reply.ogg"' in body
        assert b"OggS...." in body
