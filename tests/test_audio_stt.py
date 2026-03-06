"""Tests for Gemini-based audio transcription helper."""

import types as pytypes

import pytest

from archon.config import Config


class _FakePartFactory:
    calls = []

    @classmethod
    def from_bytes(cls, *, data, mime_type):
        cls.calls.append((data, mime_type))
        return {"kind": "audio", "len": len(data), "mime_type": mime_type}


class _FakeTypesModule:
    Part = _FakePartFactory

    class GenerateContentConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs


class _FakeModels:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class _FakeClient:
    def __init__(self, response):
        self.models = _FakeModels(response)


def test_transcribe_audio_bytes_errors_when_no_api_key(monkeypatch):
    from archon.audio import stt

    monkeypatch.setattr(stt, "load_config", lambda: Config())
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(ValueError, match="Google API key"):
        stt.transcribe_audio_bytes(b"123", "audio/ogg", client=object(), types_module=_FakeTypesModule)


def test_transcribe_audio_bytes_uses_response_text_when_present():
    from archon.audio import stt

    _FakePartFactory.calls.clear()
    response = pytypes.SimpleNamespace(text="  hello world  ")
    client = _FakeClient(response)

    text = stt.transcribe_audio_bytes(
        b"abc",
        "audio/ogg",
        api_key="key",
        client=client,
        types_module=_FakeTypesModule,
        model="gemini-test",
    )

    assert text == "hello world"
    assert _FakePartFactory.calls == [(b"abc", "audio/ogg")]
    call = client.models.calls[0]
    assert call["model"] == "gemini-test"
    assert isinstance(call["config"], _FakeTypesModule.GenerateContentConfig)


def test_transcribe_audio_bytes_falls_back_to_candidate_parts():
    from archon.audio import stt

    response = pytypes.SimpleNamespace(
        text=None,
        candidates=[
            pytypes.SimpleNamespace(
                content=pytypes.SimpleNamespace(
                    parts=[
                        pytypes.SimpleNamespace(text=None),
                        pytypes.SimpleNamespace(text="fallback transcript"),
                    ]
                )
            )
        ],
    )
    client = _FakeClient(response)

    text = stt.transcribe_audio_bytes(
        b"abc",
        "audio/mpeg",
        api_key="key",
        client=client,
        types_module=_FakeTypesModule,
    )

    assert text == "fallback transcript"
