"""Gemini-based audio transcription helpers (Phase A: Telegram voice -> text)."""

from __future__ import annotations

import os
from typing import Any

from archon.config import load_config


DEFAULT_GEMINI_STT_MODEL = "gemini-2.5-flash"
_TRANSCRIBE_PROMPT = (
    "Transcribe this audio exactly. Return plain text only, with no commentary, labels, or formatting."
)


def transcribe_audio_bytes(
    data: bytes,
    mime_type: str,
    *,
    model: str = DEFAULT_GEMINI_STT_MODEL,
    api_key: str | None = None,
    client: Any = None,
    types_module: Any = None,
) -> str:
    """Transcribe audio bytes to plain text using Gemini.

    The helper is intentionally small and testable. Callers may inject a fake client
    and types module in tests.
    """
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ValueError("Audio bytes are required")
    mime = str(mime_type or "").strip() or "audio/ogg"

    key = _resolve_google_api_key(api_key)

    if client is None:
        from google import genai

        client = genai.Client(api_key=key)
    if types_module is None:
        from google.genai import types as types_module  # type: ignore[no-redef]

    audio_part = types_module.Part.from_bytes(data=bytes(data), mime_type=mime)
    config = types_module.GenerateContentConfig(temperature=0)

    response = client.models.generate_content(
        model=model,
        contents=[_TRANSCRIBE_PROMPT, audio_part],
        config=config,
    )
    text = _extract_transcript_text(response)
    if not text:
        raise RuntimeError("Gemini returned no transcript text")
    return text


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

    raise ValueError("Missing Google API key for audio transcription (set GEMINI_API_KEY or [llm].api_key).")


def _extract_transcript_text(response: Any) -> str:
    direct = getattr(response, "text", None)
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    for candidate in _iter_seq(_get(response, "candidates")):
        content = _get(candidate, "content")
        for part in _iter_seq(_get(content, "parts")):
            text = _get(part, "text")
            if isinstance(text, str) and text.strip():
                return text.strip()
    return ""


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
