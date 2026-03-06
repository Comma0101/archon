# Telegram Voice Phase A (Voice In → Text Reply) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add Telegram voice/audio message support so Archon can receive a Telegram voice note or audio clip, transcribe it via Gemini using the existing Google API key, and reply using the normal text chat pipeline.

**Architecture:** Keep Telegram voice as a transport-layer feature. `archon/adapters/telegram.py` will detect `message.voice` / `message.audio`, download the media with new helpers in `archon/adapters/telegram_client.py`, transcribe through a small `archon/audio/stt.py` Gemini wrapper, then feed the transcript into the existing `agent.run(...)` path and return a normal text response. Internal history remains text-based; no raw audio persistence.

**Tech Stack:** Python stdlib (`urllib`, `json`), Telegram Bot API (`getFile`, file download URL), existing `google-genai` SDK (`google.genai`, `types.Part.from_bytes`, `GenerateContentConfig`), Archon Telegram adapter/client/history stack.

---

### Task 1: Write failing tests for Phase A voice flow (TDD RED)

**Files:**
- Modify: `tests/test_telegram_client.py`
- Modify: `tests/test_telegram_adapter.py`
- Create: `tests/test_audio_stt.py`

**Step 1: Add Telegram client tests (failing first)**
- `get_file(file_id)` calls `getFile` and returns result dict
- `download_file(file_path)` downloads bytes from `https://api.telegram.org/file/bot<TOKEN>/<path>`

**Step 2: Add STT wrapper tests (failing first)**
- error when no Google API key can be resolved
- success path with injected fake client/types returns cleaned transcript
- fallback text extraction from candidate parts if `response.text` is missing

**Step 3: Add Telegram adapter voice message tests (failing first)**
- voice message from allowed user:
  - downloads file
  - transcribes
  - passes transcript into agent
  - sends text reply
  - persists exchange
- invalid voice payload (missing `file_id`) returns a clear error message

**Step 4: Run targeted tests to confirm RED**

Run:
```bash
pytest tests/test_telegram_client.py tests/test_telegram_adapter.py tests/test_audio_stt.py -q
```

Expected:
- failures for missing `get_file`/`download_file`
- failures for missing audio STT module/functions
- adapter voice routing tests fail because non-text messages are ignored

---

### Task 2: Implement Gemini STT wrapper (minimal, testable)

**Files:**
- Create: `archon/audio/__init__.py`
- Create: `archon/audio/stt.py`
- Test: `tests/test_audio_stt.py`

**Step 1: Add a lightweight STT API**
- `transcribe_audio_bytes(data: bytes, mime_type: str, *, model: str = ..., api_key: str | None = None, client=None, types_module=None) -> str`
- default model should be a lightweight Gemini model appropriate for transcription-style prompts (Phase A can use a general Gemini model to keep config minimal)

**Step 2: Resolve Google API key without duplicating config logic**
- Prefer explicit `api_key` argument
- Else `load_config().llm.api_key` (works in your current Google setup)
- Else `GEMINI_API_KEY` env fallback (if config provider is not google)

**Step 3: Call Gemini with inline audio bytes**
- `types.Part.from_bytes(data=..., mime_type=...)`
- prompt text instructing plain transcript output (no commentary)
- `GenerateContentConfig(temperature=0)` (or lowest practical value)

**Step 4: Extract transcript robustly**
- prefer `response.text`
- fallback to candidate/content/parts text extraction
- strip whitespace
- raise clear error if no transcript text returned

**Step 5: Run targeted STT tests**

Run:
```bash
pytest tests/test_audio_stt.py -q
```

---

### Task 3: Extend Telegram client for file retrieval/download

**Files:**
- Modify: `archon/adapters/telegram_client.py`
- Test: `tests/test_telegram_client.py`

**Step 1: Add `get_file(file_id)`**
- call `getFile`
- return `result` dict (or empty dict)

**Step 2: Add `download_file(file_path)`**
- GET raw bytes from Telegram file endpoint:
  - `https://api.telegram.org/file/bot<TOKEN>/<file_path>`
- stdlib `urllib` only
- raise clear `RuntimeError` on HTTP/network failures

**Step 3: Run client tests**

Run:
```bash
pytest tests/test_telegram_client.py -q
```

---

### Task 4: Add Telegram voice/audio routing in adapter (Phase A behavior)

**Files:**
- Modify: `archon/adapters/telegram.py`
- Test: `tests/test_telegram_adapter.py`

**Step 1: Add voice/audio message detection**
- Accept `message.voice` and `message.audio` for allowed users
- keep command routing only for true text messages

**Step 2: Add a small adapter helper (or helpers)**
- download Telegram media (`getFile` + `download_file`)
- choose mime type (`message.voice.mime_type` / `message.audio.mime_type`, fallback defaults)
- call `archon.audio.stt.transcribe_audio_bytes(...)`

**Step 3: Feed transcript into existing chat path**
- Use transcript as `agent.run(...)` input
- Keep transcript in persisted Telegram history as text (optionally prefixed with `[voice]`)
- Send normal text reply (no TTS yet)

**Step 4: Error handling (important)**
- invalid/missing file ID -> clear user-facing message
- STT errors -> clear user-facing message (`Voice transcription error: ...`)
- keep Telegram approval UX untouched (dangerous commands from transcribed text still follow pending approval flow)

**Step 5: Run adapter tests**

Run:
```bash
pytest tests/test_telegram_adapter.py -q
```

---

### Task 5: Regression verification + context update

**Files:**
- Modify: `AGENT_CONTEXT.json`

**Step 1: Run full suite (sandbox-safe)**

Run:
```bash
HOME=/tmp PYTHONPATH=/home/comma/.local/lib/python3.14/site-packages pytest tests/ -q
```

**Step 2: Update `AGENT_CONTEXT.json`**
- New modules (`archon/audio/*`)
- Telegram voice Phase A behavior
- new tests and updated count

**Step 3: Validate JSON**

Run:
```bash
python -m json.tool AGENT_CONTEXT.json >/dev/null
```

---

### Notes / Intentional Non-Goals (Phase A)

- No TTS reply (`sendVoice`/`sendAudio`) yet (Phase B)
- No raw audio persistence
- No streaming speech recognition
- No new config subtree unless implementation friction proves necessary
- No callback-button approvals changes (already completed and should remain behavior-stable)
