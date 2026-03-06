# Telegram Voice Phase B (Text + TTS Audio Reply) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extend Telegram voice/audio support so Archon can synthesize and send a spoken reply (audio attachment) in addition to the existing text reply, using Gemini TTS and no new runtime dependencies.

**Architecture:** Keep the Phase A transcript->text chat pipeline unchanged. Add a small `archon/audio/tts.py` Gemini TTS helper that returns WAV bytes (wrapping Gemini PCM output via stdlib `wave`), add a minimal multipart upload helper to `archon/adapters/telegram_client.py` for Telegram `sendDocument`, and have `archon/adapters/telegram.py` attempt a best-effort audio reply only for inbound Telegram voice/audio messages after the normal text response is sent.

**Tech Stack:** Python stdlib (`urllib`, `json`, `wave`, `io`, `uuid`), Telegram Bot API (`sendDocument`, multipart/form-data), existing `google-genai` SDK (`GenerateContentConfig.response_modalities`, `speech_config`).

---

### Task 1: Write failing tests for TTS + Telegram media upload (TDD RED)

**Files:**
- Create: `tests/test_audio_tts.py`
- Modify: `tests/test_telegram_client.py`
- Modify: `tests/test_telegram_adapter.py`

**Step 1: Add TTS helper tests (failing first)**
- missing Google API key -> clear error
- Gemini inline audio PCM response -> returns WAV bytes + mime metadata (RIFF header)
- fallback/robust extraction from candidate parts inline audio payloads

**Step 2: Add Telegram client multipart upload tests (failing first)**
- `send_document_bytes(...)` builds multipart POST to `sendDocument`
- includes `chat_id`, `caption` (optional), and binary file part
- parses JSON result like other Telegram client helpers

**Step 3: Add Telegram adapter voice reply tests (failing first)**
- voice message path still sends text reply
- then attempts TTS synthesis and Telegram audio/document send
- TTS failure does not break text reply (best-effort fallback)

**Step 4: Run targeted tests and confirm RED**

Run:
```bash
pytest tests/test_audio_tts.py tests/test_telegram_client.py tests/test_telegram_adapter.py -q
```

---

### Task 2: Implement Gemini TTS helper (`archon/audio/tts.py`)

**Files:**
- Create: `archon/audio/tts.py`
- Modify: `archon/audio/__init__.py`
- Test: `tests/test_audio_tts.py`

**Step 1: Add minimal API**
- `synthesize_speech_wav(text: str, *, model: str = ..., voice_name: str = ..., api_key: str | None = None, client=None, types_module=None) -> tuple[bytes, str]`
- returns `(wav_bytes, "audio/wav")`

**Step 2: Resolve Google API key (same lightweight pattern as STT)**
- explicit arg -> config -> `GEMINI_API_KEY`

**Step 3: Call Gemini TTS**
- set `response_modalities=["AUDIO"]`
- set `speech_config` with prebuilt voice
- extract inline audio bytes from response (robustly)

**Step 4: Convert PCM to WAV (stdlib only)**
- wrap mono 16-bit PCM at default sample rate (24kHz) into WAV bytes via `wave`
- if response already looks like WAV, pass through

**Step 5: Run TTS tests and verify GREEN**

Run:
```bash
pytest tests/test_audio_tts.py -q
```

---

### Task 3: Add Telegram `sendDocument` bytes upload helper (multipart)

**Files:**
- Modify: `archon/adapters/telegram_client.py`
- Test: `tests/test_telegram_client.py`

**Step 1: Add stdlib multipart upload helper**
- internal helper to POST multipart/form-data bytes
- reuse Telegram error handling / JSON parsing pattern

**Step 2: Add `send_document_bytes(...)`**
- method params: `chat_id`, `filename`, `data`, optional `caption`, optional `mime_type`
- calls `sendDocument`
- returns Telegram result dict

**Step 3: Run Telegram client tests and verify GREEN**

Run:
```bash
pytest tests/test_telegram_client.py -q
```

---

### Task 4: Wire Phase B voice reply in Telegram adapter (best effort)

**Files:**
- Modify: `archon/adapters/telegram.py`
- Test: `tests/test_telegram_adapter.py`

**Step 1: Add TTS helper import + small adapter helper**
- synthesize WAV from assistant text reply
- send via `TelegramBotClient.send_document_bytes(...)`

**Step 2: Invoke only for inbound voice/audio requests**
- keep text reply path as primary
- after successful text response, attempt TTS send
- if TTS/upload fails: log to stderr only, do not send extra error text by default

**Step 3: Preserve existing approval/news/text behavior**
- no changes to request-scoped approval flow
- no changes to non-voice chat path

**Step 4: Run adapter tests and verify GREEN**

Run:
```bash
pytest tests/test_telegram_adapter.py -q
```

---

### Task 5: Full regression + context update

**Files:**
- Modify: `AGENT_CONTEXT.json`

**Step 1: Run full suite (sandbox-safe)**

Run:
```bash
HOME=/tmp PYTHONPATH=/home/comma/.local/lib/python3.14/site-packages pytest tests/ -q
```

**Step 2: Update `AGENT_CONTEXT.json`**
- add `archon/audio/tts.py`
- update Telegram adapter/client descriptions for TTS reply support
- add `tests/test_audio_tts.py`
- bump test count

**Step 3: Validate JSON**

Run:
```bash
python -m json.tool AGENT_CONTEXT.json >/dev/null
```

---

### Notes / Intentional Non-Goals (Phase B)

- No OGG/Opus conversion for true Telegram voice-note bubbles (`sendVoice`) yet
- No ffmpeg/transcoder dependency
- No TTS for regular text chat messages (voice/audio input only)
- No raw audio persistence
- No streaming TTS
