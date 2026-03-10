"""Tests for Gemini-based TTS helper."""

import base64
import types as pytypes

import pytest

from archon.config import Config


class _FakeTypesModule:
    class PrebuiltVoiceConfig:
        def __init__(self, *, voice_name=None):
            self.voice_name = voice_name

    class VoiceConfig:
        def __init__(self, *, prebuilt_voice_config=None):
            self.prebuilt_voice_config = prebuilt_voice_config

    class SpeechConfig:
        def __init__(self, *, voice_config=None, language_code=None, multi_speaker_voice_config=None):
            self.voice_config = voice_config
            self.language_code = language_code
            self.multi_speaker_voice_config = multi_speaker_voice_config

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


def test_synthesize_speech_wav_errors_when_no_api_key(monkeypatch):
    from archon.audio import tts

    monkeypatch.setattr(tts, "load_config", lambda: Config())
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(ValueError, match="Google API key"):
        tts.synthesize_speech_wav("hello", client=object(), types_module=_FakeTypesModule)


def test_synthesize_speech_wav_wraps_pcm_inline_audio_to_wav():
    from archon.audio import tts

    pcm = b"\x00\x00\x01\x00" * 10
    response = pytypes.SimpleNamespace(
        candidates=[
            pytypes.SimpleNamespace(
                content=pytypes.SimpleNamespace(
                    parts=[
                        pytypes.SimpleNamespace(
                            inline_data=pytypes.SimpleNamespace(
                                data=pcm,
                                mime_type="audio/pcm;rate=24000",
                            )
                        )
                    ]
                )
            )
        ]
    )
    client = _FakeClient(response)

    wav_bytes, mime_type = tts.synthesize_speech_wav(
        "hello there",
        api_key="key",
        client=client,
        types_module=_FakeTypesModule,
        model="gemini-tts-test",
        voice_name="Kore",
    )

    assert mime_type == "audio/wav"
    assert wav_bytes[:4] == b"RIFF"
    assert b"WAVE" in wav_bytes[:24]
    call = client.models.calls[0]
    assert call["model"] == "gemini-tts-test"
    cfg = call["config"]
    assert isinstance(cfg, _FakeTypesModule.GenerateContentConfig)
    assert cfg.kwargs["response_modalities"] == ["AUDIO"]
    assert cfg.kwargs["speech_config"].voice_config.prebuilt_voice_config.voice_name == "Kore"


def test_synthesize_speech_wav_fallback_extracts_base64_inline_audio():
    from archon.audio import tts

    wav = b"RIFFxxxxWAVEfmt "
    b64 = base64.b64encode(wav).decode("ascii")
    response = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"inline_data": {"data": b64, "mime_type": "audio/wav"}}
                    ]
                }
            }
        ]
    }
    client = _FakeClient(response)

    data, mime_type = tts.synthesize_speech_wav(
        "hello",
        api_key="key",
        client=client,
        types_module=_FakeTypesModule,
    )

    assert data == wav
    assert mime_type == "audio/wav"


def test_convert_wav_to_ogg_opus_uses_ffmpeg(monkeypatch):
    from archon.audio import tts

    calls = []

    class _Proc:
        returncode = 0
        stdout = b"OggS...."
        stderr = b""

    def fake_run(cmd, input=None, stdout=None, stderr=None, check=None):
        calls.append((cmd, input, stdout, stderr, check))
        return _Proc()

    monkeypatch.setattr(tts.subprocess, "run", fake_run)

    data, mime = tts.convert_wav_to_ogg_opus(b"RIFF....WAVE")

    assert data == b"OggS...."
    assert mime == "audio/ogg"
    cmd = calls[0][0]
    assert cmd[0] == "ffmpeg"
    assert "libopus" in cmd


def test_synthesize_speech_wav_retries_transient_internal_errors(monkeypatch):
    from archon.audio import tts

    pcm = b"\x00\x00\x01\x00" * 10
    response = pytypes.SimpleNamespace(
        candidates=[
            pytypes.SimpleNamespace(
                content=pytypes.SimpleNamespace(
                    parts=[
                        pytypes.SimpleNamespace(
                            inline_data=pytypes.SimpleNamespace(
                                data=pcm,
                                mime_type="audio/pcm;rate=24000",
                            )
                        )
                    ]
                )
            )
        ]
    )

    class _TransientModels:
        def __init__(self):
            self.calls = 0

        def generate_content(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("500 INTERNAL")
            return response

    client = pytypes.SimpleNamespace(models=_TransientModels())
    sleeps = []
    monkeypatch.setattr(tts.time, "sleep", lambda secs: sleeps.append(secs))

    wav_bytes, mime_type = tts.synthesize_speech_wav(
        "hello there",
        api_key="key",
        client=client,
        types_module=_FakeTypesModule,
    )

    assert mime_type == "audio/wav"
    assert wav_bytes[:4] == b"RIFF"
    assert client.models.calls == 2
    assert sleeps


def test_synthesize_speech_wav_removes_urls_before_synthesis():
    from archon.audio import tts

    pcm = b"\x00\x00\x01\x00" * 10
    response = pytypes.SimpleNamespace(
        candidates=[
            pytypes.SimpleNamespace(
                content=pytypes.SimpleNamespace(
                    parts=[
                        pytypes.SimpleNamespace(
                            inline_data=pytypes.SimpleNamespace(
                                data=pcm,
                                mime_type="audio/pcm;rate=24000",
                            )
                        )
                    ]
                )
            )
        ]
    )
    client = _FakeClient(response)

    tts.synthesize_speech_wav(
        "Read this https://example.com and also http://openai.com/docs right now.",
        api_key="key",
        client=client,
        types_module=_FakeTypesModule,
    )

    assert client.models.calls[0]["contents"] == "Read this and also right now."
