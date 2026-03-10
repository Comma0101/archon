"""Gemini-based text-to-speech helpers (Phase B: Telegram voice reply audio)."""

from __future__ import annotations

import base64
import io
import os
import re
import subprocess
import time
import wave
from typing import Any

from archon.config import load_config


DEFAULT_GEMINI_TTS_MODEL = "gemini-2.5-flash-preview-tts"
DEFAULT_GEMINI_TTS_VOICE = "Kore"
DEFAULT_PCM_SAMPLE_RATE = 24000
DEFAULT_TTS_RETRY_ATTEMPTS = 3
DEFAULT_TTS_RETRY_DELAY_SEC = 0.75
DEFAULT_TTS_MAX_CHARS = 600
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_MARKDOWN_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
_MARKDOWN_BULLET_RE = re.compile(r"^\s*(?:[-*•]+|\d+[.)])\s+")
_MARKDOWN_FENCE_RE = re.compile(r"^\s*```")


def synthesize_speech_wav(
    text: str,
    *,
    model: str = DEFAULT_GEMINI_TTS_MODEL,
    voice_name: str = DEFAULT_GEMINI_TTS_VOICE,
    api_key: str | None = None,
    client: Any = None,
    types_module: Any = None,
    retry_attempts: int = DEFAULT_TTS_RETRY_ATTEMPTS,
    retry_delay_sec: float = DEFAULT_TTS_RETRY_DELAY_SEC,
) -> tuple[bytes, str]:
    """Synthesize speech with Gemini and return WAV bytes + mime type."""
    prompt = normalize_tts_text(text)
    if not prompt:
        raise ValueError("Text is required for speech synthesis")

    key = _resolve_google_api_key(api_key)

    if client is None:
        from google import genai

        client = genai.Client(api_key=key)
    if types_module is None:
        from google.genai import types as types_module  # type: ignore[no-redef]

    speech_config = types_module.SpeechConfig(
        voice_config=types_module.VoiceConfig(
            prebuilt_voice_config=types_module.PrebuiltVoiceConfig(voice_name=voice_name)
        )
    )
    config = types_module.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=speech_config,
    )
    attempts = max(1, int(retry_attempts))
    last_error: Exception | None = None
    response = None
    for attempt in range(1, attempts + 1):
        try:
            response = client.models.generate_content(model=model, contents=prompt, config=config)
            break
        except Exception as e:
            last_error = e
            if attempt >= attempts or not _looks_like_transient_tts_error(e):
                raise
            time.sleep(max(0.0, float(retry_delay_sec)) * attempt)

    if response is None and last_error is not None:
        raise last_error

    audio_bytes, source_mime = _extract_inline_audio(response)
    if not audio_bytes:
        raise RuntimeError("Gemini returned no audio payload")

    normalized_mime = str(source_mime or "").lower()
    if _looks_like_wav(audio_bytes, normalized_mime):
        return bytes(audio_bytes), "audio/wav"

    sample_rate = _extract_sample_rate(normalized_mime) or DEFAULT_PCM_SAMPLE_RATE
    wav_bytes = _pcm16_mono_to_wav(bytes(audio_bytes), sample_rate=sample_rate)
    return wav_bytes, "audio/wav"


def convert_wav_to_ogg_opus(
    wav_bytes: bytes,
    *,
    ffmpeg_bin: str = "ffmpeg",
) -> tuple[bytes, str]:
    """Convert WAV bytes to OGG/Opus using ffmpeg."""
    if not isinstance(wav_bytes, (bytes, bytearray)) or not wav_bytes:
        raise ValueError("WAV bytes are required")
    try:
        proc = subprocess.run(
            [
                ffmpeg_bin,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                "pipe:0",
                "-c:a",
                "libopus",
                "-b:a",
                "32k",
                "-f",
                "ogg",
                "pipe:1",
            ],
            input=bytes(wav_bytes),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError as e:
        raise RuntimeError(f"ffmpeg not available: {e}") from e

    if proc.returncode != 0 or not proc.stdout:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffmpeg ogg/opus conversion failed (rc={proc.returncode}): {err or 'no output'}")
    return bytes(proc.stdout), "audio/ogg"


def _resolve_google_api_key(explicit_api_key: str | None) -> str:
    key = str(explicit_api_key or "").strip()
    if key:
        return key

    try:
        cfg = load_config()
        key = str(cfg.llm.api_key or "").strip()
        if key:
            return key
    except Exception:
        pass

    key = str(os.environ.get("GEMINI_API_KEY", "")).strip()
    if key:
        return key

    raise ValueError("Missing Google API key for speech synthesis (set GEMINI_API_KEY or [llm].api_key).")


def normalize_tts_text(text: str) -> str:
    """Normalize reply text for speech while preserving natural punctuation."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    no_urls = _URL_RE.sub("", raw)
    parts: list[str] = []
    in_code_block = False

    for line in no_urls.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _MARKDOWN_FENCE_RE.match(stripped):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        cleaned = _MARKDOWN_HEADING_RE.sub("", stripped)
        cleaned = _MARKDOWN_BULLET_RE.sub("", cleaned)
        cleaned = cleaned.replace("**", "").replace("__", "").replace("`", "").replace("~~", "")
        cleaned = " ".join(cleaned.split()).strip()
        if not cleaned:
            continue
        if cleaned[-1] not in ".!?":
            cleaned += "."
        parts.append(cleaned)

    normalized = " ".join(parts).strip()
    if not normalized:
        return ""
    if len(normalized) <= DEFAULT_TTS_MAX_CHARS:
        return normalized
    truncated = normalized[:DEFAULT_TTS_MAX_CHARS].rstrip()
    cut_points = [truncated.rfind(". "), truncated.rfind("! "), truncated.rfind("? "), truncated.rfind(" ")]
    cut = max(cut_points)
    if cut > 0:
        truncated = truncated[:cut].rstrip(" .!?")
    else:
        truncated = truncated.rstrip(" .!?")
    return truncated + "..."


def _extract_inline_audio(response: Any) -> tuple[bytes, str]:
    for candidate in _iter_seq(_get(response, "candidates")):
        content = _get(candidate, "content")
        for part in _iter_seq(_get(content, "parts")):
            inline_data = _get(part, "inline_data") or _get(part, "inlineData")
            if inline_data is None:
                continue
            data = _get(inline_data, "data")
            mime_type = str(_get(inline_data, "mime_type") or _get(inline_data, "mimeType") or "")
            payload = _coerce_audio_bytes(data)
            if payload:
                return payload, mime_type
    return b"", ""


def _coerce_audio_bytes(data: Any) -> bytes:
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    if isinstance(data, str):
        s = data.strip()
        if not s:
            return b""
        try:
            return base64.b64decode(s, validate=True)
        except Exception:
            return b""
    return b""


def _looks_like_wav(data: bytes, mime: str) -> bool:
    if mime and "wav" in mime:
        return True
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def _extract_sample_rate(mime: str) -> int | None:
    if not mime:
        return None
    match = re.search(r"rate=(\d+)", mime)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _looks_like_transient_tts_error(error: Exception) -> bool:
    text = str(error or "").lower()
    if not text:
        return False
    return any(token in text for token in ("500", "503", "internal", "unavailable", "deadline exceeded"))


def _pcm16_mono_to_wav(pcm_bytes: bytes, *, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit PCM
        wf.setframerate(int(sample_rate))
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def _get(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _iter_seq(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []
