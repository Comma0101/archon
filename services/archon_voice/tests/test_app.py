"""Tests for the Phase 0 Archon voice service app skeleton."""

import asyncio
import base64
import hashlib
import hmac
import time
from urllib.parse import urlencode
import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import services.archon_voice.app as voice_app

app = voice_app.app


def _transport():
    return httpx.ASGITransport(app=app)


def _manual_twilio_signature_for_pairs(url: str, pairs: list[tuple[str, str]], auth_token: str) -> str:
    payload = url + "".join(f"{k}{v}" for k, v in sorted(pairs, key=lambda item: (item[0], item[1])))
    digest = hmac.new(auth_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


@pytest.mark.anyio
async def test_health_endpoint():
    async with httpx.AsyncClient(transport=_transport(), base_url="http://testserver") as client:
        resp = await client.get("/health")

    assert resp.status_code == 200
    assert resp.json()["ok"] is True


@pytest.mark.anyio
async def test_post_missions_creates_queued_mission(monkeypatch):
    voice_app._MISSIONS.clear()
    calls = []

    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15550000000")
    monkeypatch.setenv("ARCHON_VOICE_PUBLIC_BASE_URL", "https://example.com")
    monkeypatch.setattr(
        "services.archon_voice.app.create_outbound_call",
        lambda **kwargs: calls.append(kwargs) or {"sid": "CA123", "status": "queued"},
    )

    async with httpx.AsyncClient(transport=_transport(), base_url="http://testserver") as client:
        resp = await client.post(
            "/missions",
            json={
                "call_session_id": "call_1",
                "goal": "Call me and ask about my day",
                "target_number": "+15551112222",
            },
        )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] in {"queued", "initiated"}
    assert payload["mission"]["mission_id"] == "call_1"
    assert payload["mission"]["target_number"] == "+15551112222"
    assert calls
    assert calls[0]["twiml_url"].endswith("/twilio/missions/call_1/twiml")


@pytest.mark.anyio
async def test_post_missions_preserves_mode(monkeypatch):
    voice_app._MISSIONS.clear()

    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15550000000")
    monkeypatch.setenv("ARCHON_VOICE_PUBLIC_BASE_URL", "https://example.com")
    monkeypatch.setattr(
        "services.archon_voice.app.create_outbound_call",
        lambda **kwargs: {"sid": "CA123", "status": "queued"},
    )

    async with httpx.AsyncClient(transport=_transport(), base_url="http://testserver") as client:
        resp = await client.post(
            "/missions",
            json={
                "call_session_id": "call_mode_1",
                "goal": "Call me and ask about my day",
                "target_number": "+15551112222",
                "mode": "realtime_media_stream",
            },
        )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["mission"]["mode"] == "realtime_media_stream"


@pytest.mark.anyio
async def test_post_missions_realtime_includes_think_model_debug_fields(monkeypatch):
    voice_app._MISSIONS.clear()

    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15550000000")
    monkeypatch.setenv("ARCHON_VOICE_PUBLIC_BASE_URL", "https://example.com")
    monkeypatch.setenv("ARCHON_VOICE_DEEPGRAM_THINK_PROVIDER", "open_ai")
    monkeypatch.setenv("ARCHON_VOICE_DEEPGRAM_THINK_MODEL", "gpt-4o-mini,gpt-5-mini")
    monkeypatch.setattr(
        "services.archon_voice.app.create_outbound_call",
        lambda **kwargs: {"sid": "CA123", "status": "queued"},
    )

    async with httpx.AsyncClient(transport=_transport(), base_url="http://testserver") as client:
        resp = await client.post(
            "/missions",
            json={
                "call_session_id": "call_mode_2",
                "goal": "Call me and ask about my day",
                "target_number": "+15551112222",
                "mode": "realtime_media_stream",
            },
        )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["mission"]["mode"] == "realtime_media_stream"
    assert payload["mission"]["think_provider"] == "open_ai"
    assert payload["mission"]["think_model"] == "gpt-4o-mini,gpt-5-mini"


@pytest.mark.anyio
async def test_twiml_endpoint_returns_say_response(monkeypatch):
    voice_app._MISSIONS.clear()
    voice_app._MISSIONS["call_1"] = voice_app.VoiceMission(
        mission_id="call_1",
        status="queued",
        goal="Hello from Archon",
        target_number="+15551112222",
    )

    async with httpx.AsyncClient(transport=_transport(), base_url="http://testserver") as client:
        resp = await client.post("/twilio/missions/call_1/twiml")

    assert resp.status_code == 200
    assert "text/xml" in resp.headers["content-type"]
    assert "<Say" in resp.text
    assert "Hello from Archon" in resp.text


@pytest.mark.anyio
async def test_twiml_endpoint_returns_realtime_stream_twiml_for_realtime_mode(monkeypatch):
    voice_app._MISSIONS.clear()
    monkeypatch.setenv("ARCHON_VOICE_PUBLIC_BASE_URL", "https://example.com")
    voice_app._MISSIONS["call_rt_1"] = voice_app.VoiceMission(
        mission_id="call_rt_1",
        status="queued",
        goal="Hello from Archon",
        target_number="+15551112222",
        mode="realtime_media_stream",
    )

    async with httpx.AsyncClient(transport=_transport(), base_url="http://testserver") as client:
        resp = await client.post("/twilio/missions/call_rt_1/twiml")

    assert resp.status_code == 200
    assert "text/xml" in resp.headers["content-type"]
    assert '<Pause length="1"/>' in resp.text
    assert "<Connect>" in resp.text
    assert "<Stream" in resp.text
    assert 'url="wss://example.com/twilio/missions/call_rt_1/stream"' in resp.text
    assert "<Hangup/>" in resp.text


@pytest.mark.anyio
async def test_twiml_endpoint_realtime_stream_preserves_public_base_path_prefix(monkeypatch):
    voice_app._MISSIONS.clear()
    monkeypatch.setenv("ARCHON_VOICE_PUBLIC_BASE_URL", "https://voice.example.com/base")
    voice_app._MISSIONS["call_rt_path_1"] = voice_app.VoiceMission(
        mission_id="call_rt_path_1",
        status="queued",
        goal="Hello from Archon",
        target_number="+15551112222",
        mode="realtime_media_stream",
    )

    async with httpx.AsyncClient(transport=_transport(), base_url="http://testserver") as client:
        resp = await client.post("/twilio/missions/call_rt_path_1/twiml")

    assert resp.status_code == 200
    assert "<Stream" in resp.text
    assert 'url="wss://voice.example.com/base/twilio/missions/call_rt_path_1/stream"' in resp.text


@pytest.mark.anyio
async def test_twilio_status_callback_accepts_form_encoded_payload():
    voice_app._MISSIONS.clear()
    voice_app._MISSIONS["call_1"] = voice_app.VoiceMission(
        mission_id="call_1",
        status="initiated",
        goal="Hello from Archon",
        target_number="+15551112222",
    )

    async with httpx.AsyncClient(transport=_transport(), base_url="http://testserver") as client:
        resp = await client.post(
            "/twilio/status/call_1",
            data={
                "CallStatus": "completed",
                "CallSid": "CA999",
            },
        )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is True
    assert payload["status"] == "completed"
    assert payload["mission"]["provider_call_sid"] == "CA999"
    assert voice_app._MISSIONS["call_1"].status == "completed"


@pytest.mark.anyio
async def test_twilio_status_callback_rejects_invalid_signature_when_strict_mode_enabled(monkeypatch):
    voice_app._MISSIONS.clear()
    voice_app._MISSIONS["call_1"] = voice_app.VoiceMission(
        mission_id="call_1",
        status="initiated",
        goal="Hello from Archon",
        target_number="+15551112222",
    )
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret")
    monkeypatch.setenv("ARCHON_VOICE_STRICT_TWILIO_SIGNATURE", "1")

    async with httpx.AsyncClient(transport=_transport(), base_url="http://testserver") as client:
        resp = await client.post(
            "/twilio/status/call_1",
            data={"CallStatus": "completed", "CallSid": "CA999"},
            headers={"X-Twilio-Signature": "bad"},
        )

    assert resp.status_code == 403
    assert voice_app._MISSIONS["call_1"].status == "initiated"


@pytest.mark.anyio
async def test_twilio_status_callback_accepts_valid_signature_with_repeated_form_params_in_strict_mode(monkeypatch):
    voice_app._MISSIONS.clear()
    voice_app._MISSIONS["call_dup_1"] = voice_app.VoiceMission(
        mission_id="call_dup_1",
        status="initiated",
        goal="Hello from Archon",
        target_number="+15551112222",
    )
    auth_token = "secret"
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", auth_token)
    monkeypatch.setenv("ARCHON_VOICE_STRICT_TWILIO_SIGNATURE", "1")

    form_pairs = [
        ("CallStatus", "completed"),
        ("StatusCallbackEvent", "initiated"),
        ("CallSid", "CA_DUP_999"),
        ("StatusCallbackEvent", "completed"),
    ]
    signature = _manual_twilio_signature_for_pairs(
        "http://testserver/twilio/status/call_dup_1",
        form_pairs,
        auth_token,
    )
    encoded_form = urlencode(form_pairs)

    async with httpx.AsyncClient(transport=_transport(), base_url="http://testserver") as client:
        resp = await client.post(
            "/twilio/status/call_dup_1",
            content=encoded_form,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Twilio-Signature": signature,
            },
        )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is True
    assert payload["status"] == "completed"
    assert payload["mission"]["provider_call_sid"] == "CA_DUP_999"
    assert voice_app._MISSIONS["call_dup_1"].status == "completed"


@pytest.mark.anyio
async def test_twiml_endpoint_rewrites_ask_user_goal_to_spoken_question():
    voice_app._MISSIONS.clear()
    voice_app._MISSIONS["call_1"] = voice_app.VoiceMission(
        mission_id="call_1",
        status="queued",
        goal="Ask the user how their trading is going today.",
        target_number="+15551112222",
    )

    async with httpx.AsyncClient(transport=_transport(), base_url="http://testserver") as client:
        resp = await client.post("/twilio/missions/call_1/twiml")

    assert resp.status_code == 200
    assert "Ask the user" not in resp.text
    assert "How is your trading going today?" in resp.text
    assert "<Gather" in resp.text
    assert "/twilio/missions/call_1/gather" in resp.text


@pytest.mark.anyio
async def test_twiml_endpoint_extracts_say_exactly_quoted_text():
    voice_app._MISSIONS.clear()
    voice_app._MISSIONS["call_1"] = voice_app.VoiceMission(
        mission_id="call_1",
        status="queued",
        goal="Say exactly 'hi how you doing'",
        target_number="+15551112222",
    )

    async with httpx.AsyncClient(transport=_transport(), base_url="http://testserver") as client:
        resp = await client.post("/twilio/missions/call_1/twiml")

    assert resp.status_code == 200
    assert "Say exactly" not in resp.text
    assert "hi how you doing" in resp.text
    assert "<Gather" not in resp.text


@pytest.mark.anyio
async def test_gather_endpoint_updates_transcript_and_returns_followup_twiml():
    voice_app._MISSIONS.clear()
    voice_app._MISSIONS["call_1"] = voice_app.VoiceMission(
        mission_id="call_1",
        status="initiated",
        goal="Ask the user how their trading is going today.",
        target_number="+15551112222",
        max_turns=2,
    )

    async with httpx.AsyncClient(transport=_transport(), base_url="http://testserver") as client:
        resp = await client.post(
            "/twilio/missions/call_1/gather",
            data={"SpeechResult": "Pretty good, I made two trades", "CallSid": "CA111"},
        )

    assert resp.status_code == 200
    assert "text/xml" in resp.headers["content-type"]
    assert "<Gather" in resp.text
    assert "Thanks for sharing" in resp.text
    mission = voice_app._MISSIONS["call_1"]
    assert mission.turn_count == 1
    assert mission.provider_call_sid == "CA111"
    assert mission.transcript[-1]["speaker"] == "user"
    assert "Pretty good" in mission.transcript[-1]["text"]


@pytest.mark.anyio
async def test_gather_endpoint_hangsup_after_max_turns():
    voice_app._MISSIONS.clear()
    voice_app._MISSIONS["call_1"] = voice_app.VoiceMission(
        mission_id="call_1",
        status="in_progress",
        goal="Ask the user how their trading is going today.",
        target_number="+15551112222",
        max_turns=2,
        turn_count=1,
        transcript=[{"speaker": "user", "text": "Pretty good"}],
    )

    async with httpx.AsyncClient(transport=_transport(), base_url="http://testserver") as client:
        resp = await client.post(
            "/twilio/missions/call_1/gather",
            data={"SpeechResult": "Mostly scalps today"},
        )

    assert resp.status_code == 200
    assert "<Gather" not in resp.text
    assert "<Hangup/>" in resp.text
    assert "Goodbye" in resp.text
    mission = voice_app._MISSIONS["call_1"]
    assert mission.turn_count == 2
    assert mission.status == "completed"


def test_twilio_stream_websocket_known_mission_accepts_and_updates_runtime_bridge_state():
    voice_app._MISSIONS.clear()
    if hasattr(voice_app, "_MISSION_RUNTIMES"):
        voice_app._MISSION_RUNTIMES.clear()
    voice_app._MISSIONS["call_ws_1"] = voice_app.VoiceMission(
        mission_id="call_ws_1",
        status="queued",
        goal="Talk to me",
        target_number="+15551112222",
        mode="realtime_media_stream",
    )

    with TestClient(app) as client:
        with client.websocket_connect("/twilio/missions/call_ws_1/stream") as ws:
            ws.send_json(
                {
                    "event": "connected",
                    "protocol": "Call",
                    "version": "1.0.0",
                }
            )
            ws.send_json(
                {
                    "event": "start",
                    "streamSid": "MZ1",
                    "start": {
                        "streamSid": "MZ1",
                        "callSid": "CA1",
                    },
                }
            )
            mission = voice_app._MISSIONS["call_ws_1"]
            runtime = voice_app._MISSION_RUNTIMES["call_ws_1"]
            bridge = runtime["bridge"]

            assert mission.status == "in_progress"
            assert mission.provider_call_sid == "CA1"
            assert runtime["deepgram_connected"] is False
            assert bridge.stream_sid == "MZ1"
            assert len(bridge.captured_twilio_events) == 2


def test_twilio_stream_websocket_cleans_up_runtime_and_marks_stream_disconnected_on_close():
    voice_app._MISSIONS.clear()
    if hasattr(voice_app, "_MISSION_RUNTIMES"):
        voice_app._MISSION_RUNTIMES.clear()
    voice_app._MISSIONS["call_ws_cleanup_1"] = voice_app.VoiceMission(
        mission_id="call_ws_cleanup_1",
        status="queued",
        goal="Talk to me",
        target_number="+15551112222",
        mode="realtime_media_stream",
    )

    with TestClient(app) as client:
        with client.websocket_connect("/twilio/missions/call_ws_cleanup_1/stream") as ws:
            ws.send_json(
                {
                    "event": "connected",
                    "protocol": "Call",
                    "version": "1.0.0",
                }
            )
            assert "call_ws_cleanup_1" in voice_app._MISSION_RUNTIMES
            assert voice_app._MISSIONS["call_ws_cleanup_1"].status == "in_progress"

    assert "call_ws_cleanup_1" not in voice_app._MISSION_RUNTIMES
    assert voice_app._MISSIONS["call_ws_cleanup_1"].status == "stream_disconnected"


def test_twilio_stream_websocket_unknown_mission_connect_rejected_or_closed():
    voice_app._MISSIONS.clear()
    if hasattr(voice_app, "_MISSION_RUNTIMES"):
        voice_app._MISSION_RUNTIMES.clear()

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/twilio/missions/missing/stream"):
                pass


def test_twilio_stream_websocket_relays_deepgram_audio_chunks_to_outbound_twilio_messages(monkeypatch):
    voice_app._MISSIONS.clear()
    if hasattr(voice_app, "_MISSION_RUNTIMES"):
        voice_app._MISSION_RUNTIMES.clear()

    voice_app._MISSIONS["call_ws_relay_1"] = voice_app.VoiceMission(
        mission_id="call_ws_relay_1",
        status="queued",
        goal="Talk to me about trading",
        target_number="+15551112222",
        mode="realtime_media_stream",
    )

    class _FakeDeepgramClient:
        def __init__(self) -> None:
            self.connected = False
            self.closed = False
            self.goal = None
            self.sent_audio: list[bytes] = []
            self._receive_calls = 0

        async def connect_and_initialize(self, *, goal: str | None = None) -> None:
            self.connected = True
            self.goal = goal

        async def send_audio(self, chunk: bytes) -> None:
            self.sent_audio.append(bytes(chunk))

        async def receive(self):
            self._receive_calls += 1
            if self._receive_calls == 1:
                return b"abc"
            while not self.closed:
                await asyncio.sleep(0.001)
            return None

        async def close(self) -> None:
            self.closed = True

    fake_client = _FakeDeepgramClient()
    sent_outbound: list[dict] = []
    original_send_json = voice_app.WebSocket.send_json

    async def _capture_send_json(self, data, *args, **kwargs):
        if isinstance(data, dict):
            sent_outbound.append(dict(data))
        return await original_send_json(self, data, *args, **kwargs)

    monkeypatch.setattr(
        voice_app,
        "_create_deepgram_client",
        lambda _mission: fake_client,
        raising=False,
    )
    monkeypatch.setattr(voice_app.WebSocket, "send_json", _capture_send_json)

    with TestClient(app) as client:
        with client.websocket_connect("/twilio/missions/call_ws_relay_1/stream") as ws:
            ws.send_json(
                {
                    "event": "connected",
                    "protocol": "Call",
                    "version": "1.0.0",
                }
            )
            ws.send_json(
                {
                    "event": "start",
                    "streamSid": "MZrelay1",
                    "start": {
                        "streamSid": "MZrelay1",
                        "callSid": "CArelay1",
                    },
                }
            )
            deadline = time.time() + 1.0
            while len(sent_outbound) < 2 and time.time() < deadline:
                time.sleep(0.01)
            assert len(sent_outbound) >= 2
            runtime = voice_app._MISSION_RUNTIMES["call_ws_relay_1"]
            assert runtime["deepgram_connected"] is True
            assert fake_client.connected is True

            ws.send_json(
                {
                    "event": "media",
                    "streamSid": "MZrelay1",
                    "media": {
                        "payload": "dHdp",
                    },
                }
            )
            ws.send_json(
                {
                    "event": "stop",
                    "streamSid": "MZrelay1",
                    "stop": {
                        "streamSid": "MZrelay1",
                    },
                }
            )

        mission = voice_app._MISSIONS["call_ws_relay_1"]

    outbound_events = [m.get("event") for m in sent_outbound]
    first_media = next(m for m in sent_outbound if m.get("event") == "media")
    first_mark = next(m for m in sent_outbound if m.get("event") == "mark")

    assert fake_client.goal == "Talk to me about trading"
    assert fake_client.sent_audio == [b"twi"]
    assert fake_client.closed is True
    assert outbound_events[:2] == ["media", "mark"]
    assert first_media["streamSid"] == "MZrelay1"
    assert first_media["media"]["payload"] == "YWJj"
    assert first_mark["streamSid"] == "MZrelay1"
    assert "call_ws_relay_1" not in voice_app._MISSION_RUNTIMES
    assert mission.status == "completed"


def test_twilio_stream_websocket_disconnect_contains_background_deepgram_send_errors(monkeypatch):
    voice_app._MISSIONS.clear()
    if hasattr(voice_app, "_MISSION_RUNTIMES"):
        voice_app._MISSION_RUNTIMES.clear()

    voice_app._MISSIONS["call_ws_bg_shutdown_1"] = voice_app.VoiceMission(
        mission_id="call_ws_bg_shutdown_1",
        status="queued",
        goal="Talk to me",
        target_number="+15551112222",
        mode="realtime_media_stream",
    )

    class _FakeDeepgramClient:
        def __init__(self) -> None:
            self.connected = False
            self.closed = False
            self.receive_calls = 0

        async def connect_and_initialize(self, *, goal: str | None = None) -> None:
            self.connected = True

        async def send_audio(self, chunk: bytes) -> None:
            _ = chunk

        async def receive(self):
            self.receive_calls += 1
            if self.receive_calls == 1:
                return b"abc"
            while not self.closed:
                await asyncio.sleep(0.001)
            return None

        async def close(self) -> None:
            self.closed = True

    fake_client = _FakeDeepgramClient()
    original_send_json = voice_app.WebSocket.send_json

    async def _fail_outbound_media_send(self, data, *args, **kwargs):
        if isinstance(data, dict) and data.get("event") in {"media", "mark"}:
            raise RuntimeError("simulated closed websocket")
        return await original_send_json(self, data, *args, **kwargs)

    monkeypatch.setattr(
        voice_app,
        "_create_deepgram_client",
        lambda _mission: fake_client,
        raising=False,
    )
    monkeypatch.setattr(voice_app.WebSocket, "send_json", _fail_outbound_media_send)

    with TestClient(app) as client:
        with client.websocket_connect("/twilio/missions/call_ws_bg_shutdown_1/stream") as ws:
            ws.send_json({"event": "connected", "protocol": "Call", "version": "1.0.0"})
            ws.send_json(
                {
                    "event": "start",
                    "streamSid": "MZbg1",
                    "start": {
                        "streamSid": "MZbg1",
                        "callSid": "CAbg1",
                    },
                }
            )

            deadline = time.time() + 1.0
            while fake_client.receive_calls < 1 and time.time() < deadline:
                time.sleep(0.01)
            assert fake_client.connected is True
            assert fake_client.receive_calls >= 1
            assert "call_ws_bg_shutdown_1" in voice_app._MISSION_RUNTIMES

    assert fake_client.closed is True
    assert "call_ws_bg_shutdown_1" not in voice_app._MISSION_RUNTIMES
    assert voice_app._MISSIONS["call_ws_bg_shutdown_1"].status == "stream_disconnected"


def test_twilio_stream_websocket_reconnects_once_when_deepgram_send_fails(monkeypatch):
    voice_app._MISSIONS.clear()
    if hasattr(voice_app, "_MISSION_RUNTIMES"):
        voice_app._MISSION_RUNTIMES.clear()

    voice_app._MISSIONS["call_ws_reconnect_1"] = voice_app.VoiceMission(
        mission_id="call_ws_reconnect_1",
        status="queued",
        goal="Ask for store hours",
        target_number="+15551112222",
        mode="realtime_media_stream",
    )

    class _FakeDeepgramClient:
        def __init__(self, *, fail_first_send: bool = False) -> None:
            self.fail_first_send = fail_first_send
            self._failed = False
            self.sent_audio: list[bytes] = []
            self.closed = False
            self.connect_calls = 0

        async def connect_and_initialize(self, *, goal: str | None = None) -> None:
            _ = goal
            self.connect_calls += 1

        async def send_audio(self, chunk: bytes) -> None:
            if self.fail_first_send and not self._failed:
                self._failed = True
                raise RuntimeError("simulated deepgram send failure")
            self.sent_audio.append(bytes(chunk))

        async def receive(self):
            while not self.closed:
                await asyncio.sleep(0.001)
            return None

        async def close(self) -> None:
            self.closed = True

    first = _FakeDeepgramClient(fail_first_send=True)
    second = _FakeDeepgramClient(fail_first_send=False)
    clients = [first, second]

    def _factory(_mission):
        if clients:
            return clients.pop(0)
        return second

    monkeypatch.setattr(
        voice_app,
        "_create_deepgram_client",
        _factory,
        raising=False,
    )

    with TestClient(app) as client:
        with client.websocket_connect("/twilio/missions/call_ws_reconnect_1/stream") as ws:
            ws.send_json({"event": "connected", "protocol": "Call", "version": "1.0.0"})
            ws.send_json(
                {
                    "event": "start",
                    "streamSid": "MZreconnect1",
                    "start": {
                        "streamSid": "MZreconnect1",
                        "callSid": "CAreconnect1",
                    },
                }
            )
            ws.send_json(
                {
                    "event": "media",
                    "streamSid": "MZreconnect1",
                    "media": {"payload": "AA=="},
                }
            )
            time.sleep(0.05)
            ws.send_json(
                {
                    "event": "media",
                    "streamSid": "MZreconnect1",
                    "media": {"payload": "AA=="},
                }
            )
            ws.send_json(
                {
                    "event": "stop",
                    "streamSid": "MZreconnect1",
                    "stop": {"streamSid": "MZreconnect1"},
                }
            )

    mission = voice_app._MISSIONS["call_ws_reconnect_1"]
    assert mission.status == "completed"
    assert first.connect_calls == 1
    assert second.connect_calls == 1
    assert first.closed is True
    assert second.closed is True
    assert len(second.sent_audio) >= 1
    assert "call_ws_reconnect_1" not in voice_app._MISSION_RUNTIMES


def test_twilio_stream_websocket_reconnects_multiple_times_when_enabled(monkeypatch):
    voice_app._MISSIONS.clear()
    if hasattr(voice_app, "_MISSION_RUNTIMES"):
        voice_app._MISSION_RUNTIMES.clear()

    voice_app._MISSIONS["call_ws_reconnect_multi_1"] = voice_app.VoiceMission(
        mission_id="call_ws_reconnect_multi_1",
        status="queued",
        goal="Ask for store hours",
        target_number="+15551112222",
        mode="realtime_media_stream",
    )

    class _FakeDeepgramClient:
        def __init__(self, *, fail_first_send: bool = False) -> None:
            self.fail_first_send = fail_first_send
            self._failed = False
            self.sent_audio: list[bytes] = []
            self.closed = False
            self.connect_calls = 0

        async def connect_and_initialize(self, *, goal: str | None = None) -> None:
            _ = goal
            self.connect_calls += 1

        async def send_audio(self, chunk: bytes) -> None:
            if self.fail_first_send and not self._failed:
                self._failed = True
                raise RuntimeError("simulated deepgram send failure")
            self.sent_audio.append(bytes(chunk))

        async def receive(self):
            while not self.closed:
                await asyncio.sleep(0.001)
            return None

        async def close(self) -> None:
            self.closed = True

    first = _FakeDeepgramClient(fail_first_send=True)
    second = _FakeDeepgramClient(fail_first_send=True)
    third = _FakeDeepgramClient(fail_first_send=False)
    clients = [first, second, third]

    def _factory(_mission):
        if clients:
            return clients.pop(0)
        return third

    monkeypatch.setenv("ARCHON_VOICE_DEEPGRAM_RECONNECT_MAX_ATTEMPTS", "3")
    monkeypatch.setattr(
        voice_app,
        "_create_deepgram_client",
        _factory,
        raising=False,
    )

    with TestClient(app) as client:
        with client.websocket_connect("/twilio/missions/call_ws_reconnect_multi_1/stream") as ws:
            ws.send_json({"event": "connected", "protocol": "Call", "version": "1.0.0"})
            ws.send_json(
                {
                    "event": "start",
                    "streamSid": "MZreconnectmulti1",
                    "start": {
                        "streamSid": "MZreconnectmulti1",
                        "callSid": "CAreconnectmulti1",
                    },
                }
            )
            ws.send_json(
                {
                    "event": "media",
                    "streamSid": "MZreconnectmulti1",
                    "media": {"payload": "AA=="},
                }
            )
            time.sleep(0.05)
            ws.send_json(
                {
                    "event": "media",
                    "streamSid": "MZreconnectmulti1",
                    "media": {"payload": "AA=="},
                }
            )
            time.sleep(0.05)
            ws.send_json(
                {
                    "event": "media",
                    "streamSid": "MZreconnectmulti1",
                    "media": {"payload": "AA=="},
                }
            )
            ws.send_json(
                {
                    "event": "stop",
                    "streamSid": "MZreconnectmulti1",
                    "stop": {"streamSid": "MZreconnectmulti1"},
                }
            )

    mission = voice_app._MISSIONS["call_ws_reconnect_multi_1"]
    assert mission.status == "completed"
    assert first.connect_calls == 1
    assert second.connect_calls == 1
    assert third.connect_calls == 1
    assert first.closed is True
    assert second.closed is True
    assert third.closed is True
    assert len(third.sent_audio) >= 1
    assert "call_ws_reconnect_multi_1" not in voice_app._MISSION_RUNTIMES


def test_twilio_stream_websocket_auto_completes_after_agent_farewell_without_twilio_stop(monkeypatch):
    voice_app._MISSIONS.clear()
    if hasattr(voice_app, "_MISSION_RUNTIMES"):
        voice_app._MISSION_RUNTIMES.clear()

    voice_app._MISSIONS["call_ws_goodbye_1"] = voice_app.VoiceMission(
        mission_id="call_ws_goodbye_1",
        status="queued",
        goal="Tell the user a joke, then end the call",
        target_number="+15551112222",
        mode="realtime_media_stream",
    )

    class _FakeDeepgramClient:
        def __init__(self) -> None:
            self.closed = False
            self._receive_calls = 0

        async def connect_and_initialize(self, *, goal: str | None = None) -> None:
            _ = goal

        async def send_audio(self, chunk: bytes) -> None:
            _ = chunk

        async def receive(self):
            self._receive_calls += 1
            if self._receive_calls == 1:
                return {"type": "ConversationText", "role": "assistant", "content": "Goodbye!"}
            while not self.closed:
                await asyncio.sleep(0.001)
            return None

        async def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(
        voice_app,
        "_create_deepgram_client",
        lambda _mission: _FakeDeepgramClient(),
        raising=False,
    )

    with TestClient(app) as client:
        with client.websocket_connect("/twilio/missions/call_ws_goodbye_1/stream") as ws:
            ws.send_json({"event": "connected", "protocol": "Call", "version": "1.0.0"})
            ws.send_json(
                {
                    "event": "start",
                    "streamSid": "MZgoodbye1",
                    "start": {
                        "streamSid": "MZgoodbye1",
                        "callSid": "CAgoodbye1",
                    },
                }
            )
            deadline = time.time() + 1.0
            while (
                voice_app._MISSIONS["call_ws_goodbye_1"].status != "completed"
                and time.time() < deadline
            ):
                ws.send_json(
                    {
                        "event": "media",
                        "streamSid": "MZgoodbye1",
                        "media": {"payload": "AA=="},
                    }
                )
                time.sleep(0.02)

    assert voice_app._MISSIONS["call_ws_goodbye_1"].status == "completed"
    assert "call_ws_goodbye_1" not in voice_app._MISSION_RUNTIMES


def test_twilio_stream_websocket_marks_stream_disconnected_when_deepgram_connect_times_out(monkeypatch):
    voice_app._MISSIONS.clear()
    if hasattr(voice_app, "_MISSION_RUNTIMES"):
        voice_app._MISSION_RUNTIMES.clear()

    voice_app._MISSIONS["call_ws_timeout_1"] = voice_app.VoiceMission(
        mission_id="call_ws_timeout_1",
        status="queued",
        goal="Talk to me",
        target_number="+15551112222",
        mode="realtime_media_stream",
    )

    class _SlowDeepgramClient:
        def __init__(self) -> None:
            self.closed = False

        async def connect_and_initialize(self, *, goal: str | None = None) -> None:
            _ = goal
            await asyncio.sleep(0.2)

        async def send_audio(self, chunk: bytes) -> None:
            _ = chunk

        async def receive(self):
            while not self.closed:
                await asyncio.sleep(0.001)
            return None

        async def close(self) -> None:
            self.closed = True

    monkeypatch.setenv("ARCHON_VOICE_DEEPGRAM_CONNECT_TIMEOUT_SECONDS", "0.01")
    monkeypatch.setattr(
        voice_app,
        "_create_deepgram_client",
        lambda _mission: _SlowDeepgramClient(),
        raising=False,
    )

    with TestClient(app) as client:
        with client.websocket_connect("/twilio/missions/call_ws_timeout_1/stream") as ws:
            ws.send_json({"event": "connected", "protocol": "Call", "version": "1.0.0"})
            ws.send_json(
                {
                    "event": "start",
                    "streamSid": "MZtimeout1",
                    "start": {
                        "streamSid": "MZtimeout1",
                        "callSid": "CAtimeout1",
                    },
                }
            )
            deadline = time.time() + 0.5
            while (
                voice_app._MISSIONS["call_ws_timeout_1"].status != "stream_disconnected"
                and time.time() < deadline
            ):
                time.sleep(0.01)

    assert voice_app._MISSIONS["call_ws_timeout_1"].status == "stream_disconnected"
    assert "call_ws_timeout_1" not in voice_app._MISSION_RUNTIMES


def test_get_mission_realtime_status_includes_transcript_and_realtime_fields(monkeypatch):
    voice_app._MISSIONS.clear()
    if hasattr(voice_app, "_MISSION_RUNTIMES"):
        voice_app._MISSION_RUNTIMES.clear()

    voice_app._MISSIONS["call_ws_status_1"] = voice_app.VoiceMission(
        mission_id="call_ws_status_1",
        status="queued",
        goal="Talk to me about trading",
        target_number="+15551112222",
        mode="realtime_media_stream",
    )

    class _FakeDeepgramClient:
        def __init__(self) -> None:
            self.closed = False
            self.receive_calls = 0

        async def connect_and_initialize(self, *, goal: str | None = None) -> None:
            _ = goal

        async def send_audio(self, chunk: bytes) -> None:
            _ = chunk

        async def receive(self):
            self.receive_calls += 1
            if self.receive_calls == 1:
                return {"type": "ConversationText", "role": "user", "content": "hello there"}
            if self.receive_calls == 2:
                return {"type": "ConversationText", "role": "assistant", "content": "hi from archon"}
            while not self.closed:
                await asyncio.sleep(0.001)
            return None

        async def close(self) -> None:
            self.closed = True

    fake_client = _FakeDeepgramClient()
    monkeypatch.setattr(
        voice_app,
        "_create_deepgram_client",
        lambda _mission: fake_client,
        raising=False,
    )

    with TestClient(app) as client:
        with client.websocket_connect("/twilio/missions/call_ws_status_1/stream") as ws:
            ws.send_json({"event": "connected", "protocol": "Call", "version": "1.0.0"})
            ws.send_json(
                {
                    "event": "start",
                    "streamSid": "MZstatus1",
                    "start": {
                        "streamSid": "MZstatus1",
                        "callSid": "CAstatus1",
                    },
                }
            )

            deadline = time.time() + 1.0
            while len(voice_app._MISSIONS["call_ws_status_1"].transcript) < 2 and time.time() < deadline:
                time.sleep(0.01)

            ws.send_json(
                {
                    "event": "stop",
                    "streamSid": "MZstatus1",
                    "stop": {"streamSid": "MZstatus1"},
                }
            )

        resp = client.get("/missions/call_ws_status_1")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is True
    assert payload["mission"]["mode"] == "realtime_media_stream"
    assert payload["mission"]["voice_backend"] == "deepgram_voice_agent_v1"
    assert payload["mission"]["twilio_stream_sid"] == "MZstatus1"
    assert payload["mission"]["provider_call_sid"] == "CAstatus1"
    assert len(payload["mission"]["transcript"]) >= 2
    assert payload["mission"]["transcript"][0]["speaker"] == "user"
    assert payload["mission"]["transcript"][0]["text"] == "hello there"
