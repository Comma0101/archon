# Korami Voice Agent Realtime (Twilio + Deepgram) Phase 2 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a realtime conversational phone-call path using Twilio Media Streams (`<Connect><Stream>`) bridged to Deepgram Voice Agent API V1, while preserving the existing scripted `<Gather>` fallback and keeping Archon core lightweight.

**Architecture:** Extend `services/archon_voice/` with a realtime media-plane path (Twilio WS endpoint + Deepgram WS adapter + bridge runtime). Archon remains the control plane (`archon/calls/*`, tools, approvals, mission state) and selects between `scripted_gather` and `realtime_media_stream` modes via mission payload/config.

**Tech Stack:** Python stdlib (`json`, `dataclasses`, `base64`, `time`, `asyncio`), FastAPI WebSockets, existing `services/archon_voice` service, Twilio Programmable Voice Media Streams (bidirectional), Deepgram Voice Agent API V1 WebSocket (`wss://agent.deepgram.com/v1/agent/converse`), file-based mission state.

---

### Task 1: Add realtime mission mode and config shape in Archon + service models (TDD RED)

**Files:**
- Modify: `archon/calls/models.py`
- Modify: `archon/config.py`
- Modify: `services/archon_voice/models.py`
- Test: `tests/test_calls_store.py`
- Test: `tests/test_config.py`
- Test: `services/archon_voice/tests/test_app.py`

**Step 1: Write failing Archon tests for mission mode serialization**

```python
def test_call_mission_roundtrip_realtime_mode():
    mission = CallMission(
        call_session_id="call_rt_1",
        goal="Ask about today's trading session",
        target_number="+15551234567",
        status="queued",
        mode="realtime_media_stream",
    )
    restored = CallMission.from_dict(mission.to_dict())
    assert restored.mode == "realtime_media_stream"
```

**Step 2: Write failing config tests for realtime defaults**

```python
def test_load_config_calls_realtime_defaults():
    cfg = load_config()
    assert cfg.calls.realtime.enabled is False
    assert cfg.calls.realtime.provider in {"deepgram_voice_agent_v1"}
```

**Step 3: Write failing service test for mission payload mode persistence**

```python
def test_create_mission_accepts_realtime_mode(client):
    resp = client.post("/missions", json={"call_session_id": "call1", "goal": "x", "target_number": "+1555", "mode": "realtime_media_stream"})
    assert resp.status_code == 200
    assert resp.json()["mission"]["mode"] == "realtime_media_stream"
```

**Step 4: Run tests to confirm RED**

Run:
```bash
pytest tests/test_calls_store.py tests/test_config.py -q
services/archon_voice/.venv/bin/pytest services/archon_voice/tests/test_app.py -q
```

**Step 5: Implement minimal mode/config fields**
- Add `CallMission.mode` with default `scripted_gather`
- Add `calls.realtime` config section (disabled by default)
- Add service-side mission model support for `mode`

**Step 6: Run tests to verify GREEN**

Run:
```bash
pytest tests/test_calls_store.py tests/test_config.py -q
services/archon_voice/.venv/bin/pytest services/archon_voice/tests/test_app.py -q
```

**Step 7: Commit**

```bash
git add archon/calls/models.py archon/config.py services/archon_voice/models.py tests/test_calls_store.py tests/test_config.py services/archon_voice/tests/test_app.py
git commit -m "feat: add realtime call mission mode config"
```

---

### Task 2: Add realtime TwiML builder for `<Connect><Stream>` mission path (TDD RED)

**Files:**
- Modify: `services/archon_voice/twiml.py`
- Modify: `services/archon_voice/app.py`
- Test: `services/archon_voice/tests/test_twiml.py`
- Test: `services/archon_voice/tests/test_app.py`

**Step 1: Write failing TwiML unit test for `<Connect><Stream>`**

```python
def test_build_realtime_stream_twiml():
    xml = build_realtime_stream_twiml("wss://voice.example.com/twilio/missions/call1/stream")
    assert "<Connect>" in xml
    assert "<Stream" in xml
    assert 'url="wss://voice.example.com/twilio/missions/call1/stream"' in xml
```

**Step 2: Write failing app test for realtime mission TwiML route**

```python
def test_mission_twi_ml_realtime_mode_returns_stream(client):
    client.post("/missions", json={"call_session_id": "call1", "goal": "x", "target_number": "+1555", "mode": "realtime_media_stream"})
    resp = client.post("/twilio/missions/call1/twiml")
    assert resp.status_code == 200
    assert "<Stream" in resp.text
```

**Step 3: Run tests to confirm RED**

Run:
```bash
services/archon_voice/.venv/bin/pytest services/archon_voice/tests/test_twiml.py services/archon_voice/tests/test_app.py -q
```

**Step 4: Implement minimal realtime TwiML**
- Add `build_realtime_stream_twiml(stream_url, status_callback=None)` helper
- Branch mission TwiML route on `mode == "realtime_media_stream"`
- Use `wss://` URL derived from public base URL + mission stream endpoint

**Step 5: Run tests to verify GREEN**

Run:
```bash
services/archon_voice/.venv/bin/pytest services/archon_voice/tests/test_twiml.py services/archon_voice/tests/test_app.py -q
```

**Step 6: Commit**

```bash
git add services/archon_voice/twiml.py services/archon_voice/app.py services/archon_voice/tests/test_twiml.py services/archon_voice/tests/test_app.py
git commit -m "feat: add realtime stream twiml path"
```

---

### Task 3: Add Twilio Media Streams WebSocket message helpers (TDD RED)

**Files:**
- Create: `services/archon_voice/twilio_stream.py`
- Test: `services/archon_voice/tests/test_twilio_stream.py`

**Step 1: Write failing tests for parsing inbound Twilio events**

```python
def test_parse_twilio_media_message():
    event = parse_twilio_stream_message({"event": "media", "streamSid": "MZ1", "media": {"payload": "YWJj"}})
    assert event.event == "media"
    assert event.stream_sid == "MZ1"
    assert event.payload_b64 == "YWJj"
```

```python
def test_parse_twilio_mark_message():
    event = parse_twilio_stream_message({"event": "mark", "streamSid": "MZ1", "mark": {"name": "chunk1"}})
    assert event.event == "mark"
    assert event.mark_name == "chunk1"
```

**Step 2: Write failing tests for outbound serialization**

```python
def test_build_twilio_media_outbound_message():
    msg = build_twilio_media_message(stream_sid="MZ1", payload_b64="YWJj")
    assert msg["event"] == "media"
    assert msg["streamSid"] == "MZ1"
```

def test_build_twilio_clear_message():
    assert build_twilio_clear_message("MZ1")["event"] == "clear"
```

**Step 3: Run tests to confirm RED**

Run:
```bash
services/archon_voice/.venv/bin/pytest services/archon_voice/tests/test_twilio_stream.py -q
```

**Step 4: Implement minimal Twilio stream helpers**
- Dataclasses for normalized events (`connected`, `start`, `media`, `mark`, `dtmf`, `stop`)
- Parse/validate helpers (strict but lightweight)
- Builders for outbound `media`, `mark`, `clear`

**Step 5: Run tests to verify GREEN**

Run:
```bash
services/archon_voice/.venv/bin/pytest services/archon_voice/tests/test_twilio_stream.py -q
```

**Step 6: Commit**

```bash
git add services/archon_voice/twilio_stream.py services/archon_voice/tests/test_twilio_stream.py
git commit -m "feat: add twilio media stream protocol helpers"
```

---

### Task 4: Add Deepgram Voice Agent WebSocket adapter (message layer only) (TDD RED)

**Files:**
- Create: `services/archon_voice/deepgram_agent.py`
- Test: `services/archon_voice/tests/test_deepgram_agent.py`

**Step 1: Write failing tests for settings message generation**

```python
def test_build_deepgram_settings_for_twilio_mulaw():
    payload = build_deepgram_settings(goal="Ask how trading went")
    assert payload["type"] == "Settings"
    assert payload["audio"]["input"]["encoding"] == "mulaw"
    assert payload["audio"]["input"]["sample_rate"] == 8000
    assert payload["audio"]["output"]["encoding"] == "mulaw"
```

**Step 2: Write failing tests for event normalization**

```python
def test_parse_deepgram_text_event():
    evt = parse_deepgram_json_event({"type": "ConversationText", "role": "user", "content": "hi"})
    assert evt.type == "ConversationText"
    assert evt.role == "user"
```

**Step 3: Write failing tests for keepalive helper**

```python
def test_build_keepalive_message():
    assert build_keepalive_message() == {"type": "KeepAlive"}
```

**Step 4: Run tests to confirm RED**

Run:
```bash
services/archon_voice/.venv/bin/pytest services/archon_voice/tests/test_deepgram_agent.py -q
```

**Step 5: Implement minimal Deepgram adapter helpers**
- `build_deepgram_settings(...)`
- `parse_deepgram_json_event(...)`
- `build_keepalive_message()`
- no live socket client yet

**Step 6: Run tests to verify GREEN**

Run:
```bash
services/archon_voice/.venv/bin/pytest services/archon_voice/tests/test_deepgram_agent.py -q
```

**Step 7: Commit**

```bash
git add services/archon_voice/deepgram_agent.py services/archon_voice/tests/test_deepgram_agent.py
git commit -m "feat: add deepgram voice agent protocol helpers"
```

---

### Task 5: Add realtime bridge state machine (no FastAPI endpoint yet) (TDD RED)

**Files:**
- Create: `services/archon_voice/realtime_bridge.py`
- Test: `services/archon_voice/tests/test_realtime_bridge.py`

**Step 1: Write failing tests for Twilio->Deepgram audio forwarding**

```python
@pytest.mark.asyncio
async def test_bridge_forwards_twilio_media_to_deepgram():
    twilio_in = [{"event": "start", "start": {"streamSid": "MZ1"}}, {"event": "media", "media": {"payload": "YWJj"}, "streamSid": "MZ1"}]
    bridge = RealtimeBridge(...)
    await bridge.handle_twilio_event_dict(twilio_in[0])
    await bridge.handle_twilio_event_dict(twilio_in[1])
    assert bridge.deepgram_audio_bytes_sent > 0
```

**Step 2: Write failing tests for Deepgram->Twilio audio output**

```python
@pytest.mark.asyncio
async def test_bridge_emits_twilio_media_and_mark_from_deepgram_audio():
    bridge = RealtimeBridge(...)
    msgs = await bridge.handle_deepgram_audio_chunk(b"abc")
    assert any(m["event"] == "media" for m in msgs)
    assert any(m["event"] == "mark" for m in msgs)
```

**Step 3: Write failing tests for barge-in clear behavior**

```python
def test_bridge_requests_clear_when_user_starts_speaking_mid_agent_audio():
    bridge = RealtimeBridge(...)
    bridge.agent_audio_in_flight = True
    msgs = bridge.handle_deepgram_event({"type": "UserStartedSpeaking"})
    assert any(m["event"] == "clear" for m in msgs)
```

**Step 4: Run tests to confirm RED**

Run:
```bash
services/archon_voice/.venv/bin/pytest services/archon_voice/tests/test_realtime_bridge.py -q
```

**Step 5: Implement minimal bridge state**
- Track `stream_sid`
- Audio in/out counters
- `agent_audio_in_flight` state
- Generate outbound Twilio `media`/`mark`/`clear`
- Transcript event capture hooks (in-memory)

**Step 6: Run tests to verify GREEN**

Run:
```bash
services/archon_voice/.venv/bin/pytest services/archon_voice/tests/test_realtime_bridge.py -q
```

**Step 7: Commit**

```bash
git add services/archon_voice/realtime_bridge.py services/archon_voice/tests/test_realtime_bridge.py
git commit -m "feat: add realtime bridge state machine"
```

---

### Task 6: Add FastAPI Twilio media WebSocket endpoint and mission wiring (TDD RED)

**Files:**
- Modify: `services/archon_voice/app.py`
- Modify: `services/archon_voice/models.py`
- Test: `services/archon_voice/tests/test_app.py`

**Step 1: Write failing WebSocket app test for Twilio stream connect**

```python
@pytest.mark.asyncio
async def test_twilio_stream_websocket_accepts_and_handles_connected_start_messages(async_client):
    async with async_client.websocket_connect("/twilio/missions/call1/stream") as ws:
        await ws.send_json({"event": "connected", "protocol": "Call", "version": "1.0.0"})
        await ws.send_json({"event": "start", "streamSid": "MZ1", "start": {"streamSid": "MZ1"}})
        # endpoint may not reply immediately; assert no exception and mission state updated
```

**Step 2: Write failing test for missing mission returns close/error**

```python
@pytest.mark.asyncio
async def test_twilio_stream_websocket_unknown_mission_rejected(async_client):
    with pytest.raises(Exception):
        async with async_client.websocket_connect("/twilio/missions/missing/stream"):
            ...
```

**Step 3: Run tests to confirm RED**

Run:
```bash
services/archon_voice/.venv/bin/pytest services/archon_voice/tests/test_app.py -q
```

**Step 4: Implement minimal Twilio WS endpoint**
- Add `/twilio/missions/{mission_id}/stream` WebSocket
- Accept connection, parse Twilio messages with `twilio_stream.py`
- Attach `RealtimeBridge` to mission
- Stub Deepgram connection (no live networking yet)

**Step 5: Run tests to verify GREEN**

Run:
```bash
services/archon_voice/.venv/bin/pytest services/archon_voice/tests/test_app.py -q
```

**Step 6: Commit**

```bash
git add services/archon_voice/app.py services/archon_voice/models.py services/archon_voice/tests/test_app.py
git commit -m "feat: add twilio media stream websocket endpoint"
```

---

### Task 7: Add Deepgram WebSocket runtime client and bridge loop integration (TDD RED)

**Files:**
- Modify: `services/archon_voice/deepgram_agent.py`
- Modify: `services/archon_voice/realtime_bridge.py`
- Modify: `services/archon_voice/app.py`
- Test: `services/archon_voice/tests/test_deepgram_agent.py`
- Test: `services/archon_voice/tests/test_realtime_bridge.py`
- Test: `services/archon_voice/tests/test_app.py`

**Step 1: Write failing adapter tests with fake WebSocket transport**

```python
@pytest.mark.asyncio
async def test_deepgram_client_sends_settings_then_keepalive(fake_ws):
    client = DeepgramVoiceAgentClient(ws_factory=lambda *_: fake_ws)
    await client.connect_and_initialize(goal="Ask about trading")
    assert fake_ws.sent_json[0]["type"] == "Settings"
```

**Step 2: Write failing bridge/app test for relay of Deepgram audio -> Twilio outbound media**

```python
@pytest.mark.asyncio
async def test_twilio_endpoint_relays_deepgram_audio_chunks(async_client, monkeypatch):
    # patch Deepgram client with fake that yields audio bytes
    ...
    assert outbound_twilio_message["event"] == "media"
```

**Step 3: Run tests to confirm RED**

Run:
```bash
services/archon_voice/.venv/bin/pytest services/archon_voice/tests/test_deepgram_agent.py services/archon_voice/tests/test_realtime_bridge.py services/archon_voice/tests/test_app.py -q
```

**Step 4: Implement minimal live Deepgram WS client**
- Connect to `wss://agent.deepgram.com/v1/agent/converse`
- Send `Settings`
- Send periodic `KeepAlive`
- Expose async send/receive APIs for JSON + binary audio
- Make transport injectable for tests

**Step 5: Integrate app endpoint bridge loop**
- Start Twilio receive loop + Deepgram receive loop
- Forward audio both ways
- Close cleanly on Twilio `stop` / WS disconnect
- Update mission status fields

**Step 6: Run tests to verify GREEN**

Run:
```bash
services/archon_voice/.venv/bin/pytest services/archon_voice/tests/test_deepgram_agent.py services/archon_voice/tests/test_realtime_bridge.py services/archon_voice/tests/test_app.py -q
```

**Step 7: Commit**

```bash
git add services/archon_voice/deepgram_agent.py services/archon_voice/realtime_bridge.py services/archon_voice/app.py services/archon_voice/tests/test_deepgram_agent.py services/archon_voice/tests/test_realtime_bridge.py services/archon_voice/tests/test_app.py
git commit -m "feat: bridge twilio media streams to deepgram voice agent"
```

---

### Task 8: Add transcript/status persistence and Archon visibility for realtime missions (TDD RED)

**Files:**
- Modify: `services/archon_voice/models.py`
- Modify: `services/archon_voice/app.py`
- Modify: `archon/calls/store.py`
- Modify: `archon/calls/service_client.py`
- Modify: `archon/calls/runner.py`
- Test: `services/archon_voice/tests/test_app.py`
- Test: `tests/test_calls_service_client.py`
- Test: `tests/test_tools_calls_missions.py`

**Step 1: Write failing service test for transcript accumulation during realtime session**

```python
def test_realtime_mission_status_includes_transcript_entries(client):
    # seed mission and transcript entries
    resp = client.get("/missions/call1")
    assert "transcript" in resp.json()["mission"]
```

**Step 2: Write failing Archon client/tool test for surfaced realtime status**

```python
def test_call_mission_status_surfaces_realtime_fields(monkeypatch):
    result = run_call_mission_status(...)
    assert "mode" in result
    assert "voice_backend" in result
```

**Step 3: Run tests to confirm RED**

Run:
```bash
pytest tests/test_calls_service_client.py tests/test_tools_calls_missions.py -q
services/archon_voice/.venv/bin/pytest services/archon_voice/tests/test_app.py -q
```

**Step 4: Implement minimal persistence/reporting**
- Store transcript turns and key realtime IDs in mission state
- Return fields from service `/missions/{id}`
- Surface fields through Archon client + tools

**Step 5: Run tests to verify GREEN**

Run:
```bash
pytest tests/test_calls_service_client.py tests/test_tools_calls_missions.py -q
services/archon_voice/.venv/bin/pytest services/archon_voice/tests/test_app.py -q
```

**Step 6: Commit**

```bash
git add services/archon_voice/models.py services/archon_voice/app.py archon/calls/store.py archon/calls/service_client.py archon/calls/runner.py tests/test_calls_service_client.py tests/test_tools_calls_missions.py services/archon_voice/tests/test_app.py
git commit -m "feat: persist realtime mission transcript and status"
```

---

### Task 9: Add Twilio request signature validation (HTTP + WS handshake path) (TDD RED)

**Files:**
- Create: `services/archon_voice/security.py`
- Modify: `services/archon_voice/app.py`
- Test: `services/archon_voice/tests/test_security.py`
- Test: `services/archon_voice/tests/test_app.py`

**Step 1: Write failing unit tests for signature verification helper**

```python
def test_verify_twilio_signature_accepts_valid_signature():
    assert verify_twilio_signature(url, params, signature, auth_token="secret") is True

def test_verify_twilio_signature_rejects_invalid_signature():
    assert verify_twilio_signature(url, params, "bad", auth_token="secret") is False
```

**Step 2: Write failing app tests for rejected unsigned callbacks in strict mode**

```python
def test_status_callback_rejects_invalid_signature_when_enabled(client):
    resp = client.post("/twilio/status/call1", data={"CallStatus": "completed"}, headers={"X-Twilio-Signature": "bad"})
    assert resp.status_code == 403
```

**Step 3: Run tests to confirm RED**

Run:
```bash
services/archon_voice/.venv/bin/pytest services/archon_voice/tests/test_security.py services/archon_voice/tests/test_app.py -q
```

**Step 4: Implement signature validation**
- stdlib HMAC-SHA1 validation helper matching Twilio request signing rules
- Config gate: `strict_twilio_signature = false` by default in dev
- Apply to HTTP callbacks first; add WS handshake/query/header validation path as supported by framework

**Step 5: Run tests to verify GREEN**

Run:
```bash
services/archon_voice/.venv/bin/pytest services/archon_voice/tests/test_security.py services/archon_voice/tests/test_app.py -q
```

**Step 6: Commit**

```bash
git add services/archon_voice/security.py services/archon_voice/app.py services/archon_voice/tests/test_security.py services/archon_voice/tests/test_app.py
git commit -m "feat: validate twilio callback signatures"
```

---

### Task 10: Add config/tool routing to select realtime mode and fallback behavior (TDD RED)

**Files:**
- Modify: `archon/calls/runner.py`
- Modify: `archon/tooling/call_mission_tools.py`
- Modify: `tests/test_tools_calls_missions.py`
- Modify: `services/archon_voice/app.py`
- Test: `services/archon_voice/tests/test_app.py`

**Step 1: Write failing tests for realtime mode selection**

```python
def test_call_mission_start_uses_realtime_mode_when_enabled(monkeypatch):
    result = run_call_mission_start(...)
    assert result["mode"] == "realtime_media_stream"
```

**Step 2: Write failing tests for fallback to scripted on realtime disabled/error**

```python
def test_call_mission_start_falls_back_to_scripted_when_realtime_unavailable(monkeypatch):
    result = run_call_mission_start(...)
    assert result["mode"] == "scripted_gather"
    assert result["fallback_used"] is True
```

**Step 3: Run tests to confirm RED**

Run:
```bash
pytest tests/test_tools_calls_missions.py -q
services/archon_voice/.venv/bin/pytest services/archon_voice/tests/test_app.py -q
```

**Step 4: Implement minimal mode routing + fallback**
- Config/feature-flag driven mode selection
- Include `mode` and `fallback_used` in tool output
- Preserve explicit user confirmation and current safety behavior

**Step 5: Run tests to verify GREEN**

Run:
```bash
pytest tests/test_tools_calls_missions.py -q
services/archon_voice/.venv/bin/pytest services/archon_voice/tests/test_app.py -q
```

**Step 6: Commit**

```bash
git add archon/calls/runner.py archon/tooling/call_mission_tools.py tests/test_tools_calls_missions.py services/archon_voice/app.py services/archon_voice/tests/test_app.py
git commit -m "feat: add realtime mode selection and fallback"
```

---

### Task 11: Docs + local ops hardening (systemd user services, tunnel notes, manual test checklist)

**Files:**
- Modify: `services/archon_voice/README.md`
- Create: `services/archon_voice/deploy/archon-voice.service.example`
- Create: `services/archon_voice/deploy/cloudflared-archon-voice.service.example`
- Test: `services/archon_voice/tests/test_app.py` (if README examples affect behavior, otherwise no code tests)

**Step 1: Write a manual verification checklist in README (no code changes yet)**
- Realtime env vars (`DEEPGRAM_API_KEY`, Twilio creds, base URL)
- Uvicorn + cloudflared/systemd startup
- Public `/health` + Twilio TwiML verification
- Live call smoke test and expected logs

**Step 2: Add example `systemd --user` unit files**
- `archon_voice` service
- `cloudflared` tunnel service

**Step 3: Run verification commands (docs examples compile/sanity)**

Run:
```bash
python -m json.tool AGENT_CONTEXT.json >/dev/null
services/archon_voice/.venv/bin/pytest services/archon_voice/tests -q
pytest tests/test_tools_calls_missions.py tests/test_tools_calls_service.py -q
```

**Step 4: Commit**

```bash
git add services/archon_voice/README.md services/archon_voice/deploy/archon-voice.service.example services/archon_voice/deploy/cloudflared-archon-voice.service.example
git commit -m "docs: add realtime voice service ops and systemd examples"
```

---

### Task 12: Full verification and milestone context update

**Files:**
- Modify: `AGENT_CONTEXT.json`

**Step 1: Run targeted and full test suites**

Run:
```bash
services/archon_voice/.venv/bin/pytest services/archon_voice/tests -q
HOME=/tmp PYTHONPATH=/home/comma/.local/lib/python3.14/site-packages pytest tests/ -q
```

**Step 2: Manual live smoke test**
- Start `uvicorn` + `cloudflared`
- Place one realtime call
- Confirm bidirectional conversation + barge-in + final status

**Step 3: Update `AGENT_CONTEXT.json` only after verified milestone**
- Add realtime mode, Twilio Media Streams bridge, Deepgram Voice Agent integration, test counts, known caveats

**Step 4: Validate context JSON**

Run:
```bash
python -m json.tool AGENT_CONTEXT.json >/dev/null
```

**Step 5: Commit**

```bash
git add AGENT_CONTEXT.json
git commit -m "docs: update context for realtime twilio deepgram voice path"
```

