"""FastAPI service skeleton for Archon voice runtime (Phase 0)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import time
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect

from services.archon_voice import deepgram_agent
from services.archon_voice.models import VoiceMission
from services.archon_voice.realtime_bridge import RealtimeBridge
from services.archon_voice.security import verify_twilio_signature
from services.archon_voice.twilio_client import create_outbound_call
from services.archon_voice.twiml import (
    build_gather_twiml,
    build_realtime_stream_twiml,
    build_say_twiml,
)


app = FastAPI(title="Archon Voice Service", version="0.1.0-phase0")
_MISSIONS: dict[str, VoiceMission] = {}
_MISSION_RUNTIMES: dict[str, dict[str, object]] = {}
_LOG = logging.getLogger("archon_voice.app")


def _env(name: str) -> str:
    return str(os.environ.get(name, "")).strip()


def _public_base_url() -> str:
    return _env("ARCHON_VOICE_PUBLIC_BASE_URL").rstrip("/")


def _env_bool(name: str, *, default: bool = False) -> bool:
    value = _env(name)
    if not value:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _strict_twilio_signature_enabled() -> bool:
    return _env_bool("ARCHON_VOICE_STRICT_TWILIO_SIGNATURE", default=False)


def _deepgram_connect_timeout_seconds() -> float:
    raw = _env("ARCHON_VOICE_DEEPGRAM_CONNECT_TIMEOUT_SECONDS")
    if not raw:
        return 8.0
    try:
        value = float(raw)
    except ValueError:
        return 8.0
    return max(0.1, value)


def _deepgram_reconnect_max_attempts() -> int:
    raw = _env("ARCHON_VOICE_DEEPGRAM_RECONNECT_MAX_ATTEMPTS")
    if not raw:
        return 3
    try:
        value = int(raw)
    except ValueError:
        return 3
    return max(1, value)


def _deepgram_think_debug_config() -> tuple[str, str]:
    provider = _env("ARCHON_VOICE_DEEPGRAM_THINK_PROVIDER") or "open_ai"
    model = _env("ARCHON_VOICE_DEEPGRAM_THINK_MODEL") or "gpt-4o-mini"
    return provider, model


def _twilio_enabled() -> bool:
    return all(
        [
            _env("TWILIO_ACCOUNT_SID"),
            _env("TWILIO_AUTH_TOKEN"),
            _env("TWILIO_FROM_NUMBER"),
            _public_base_url(),
        ]
    )


def _mission_twiml_url(mission_id: str) -> str:
    base = _public_base_url()
    return f"{base}/twilio/missions/{mission_id}/twiml" if base else ""


def _status_callback_url(mission_id: str) -> str:
    base = _public_base_url()
    return f"{base}/twilio/status/{mission_id}" if base else ""


def _mission_gather_url(mission_id: str) -> str:
    base = _public_base_url()
    path = f"/twilio/missions/{mission_id}/gather"
    return f"{base}{path}" if base else path


def _mission_stream_ws_url(mission_id: str) -> str:
    base = _public_base_url()
    path = f"/twilio/missions/{mission_id}/stream"
    if not base:
        return path

    parts = urlsplit(base)
    scheme = parts.scheme.lower()
    ws_scheme = "wss" if scheme == "https" else "ws" if scheme == "http" else scheme
    base_path = (parts.path or "").rstrip("/")
    return urlunsplit((ws_scheme, parts.netloc, f"{base_path}{path}", "", ""))


def _dequote(text: str) -> str:
    value = str(text or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1].strip()
    return value


def _second_personize(text: str) -> str:
    out = str(text or "")
    replacements = [
        (r"\bthe user\b", "you"),
        (r"\btheir\b", "your"),
        (r"\btheirs\b", "yours"),
        (r"\bthem\b", "you"),
        (r"\bthey are\b", "you are"),
        (r"\bthey're\b", "you're"),
        (r"\bthey\b", "you"),
    ]
    for pattern, repl in replacements:
        out = re.sub(pattern, repl, out, flags=re.IGNORECASE)
    return out


def _normalize_goal_for_speech(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""

    m = re.match(r"^(?:please\s+)?(?:call\s+\w+\s+and\s+)?say\s+exactly\s+(.+)$", raw, flags=re.IGNORECASE)
    if m:
        spoken = _dequote(m.group(1))
        return spoken or raw

    m = re.match(r"^(?:please\s+)?ask\s+(?:the\s+user|them)\s+(.+)$", raw, flags=re.IGNORECASE)
    if m:
        body = _second_personize(m.group(1).strip())
        if body:
            body = re.sub(r"^\s*how\s+your\b", "How is your", body, flags=re.IGNORECASE)
            body = re.sub(r"^\s*how\s+you\b", "How are you", body, flags=re.IGNORECASE)
            body = re.sub(
                r"^(How is your .+?)\s+is going\b",
                r"\1 going",
                body,
                flags=re.IGNORECASE,
            )
            body = body[:1].upper() + body[1:]
            if body.endswith("."):
                body = body[:-1] + "?"
            elif not body.endswith(("?", "!")):
                body = body + "?"
            return f"Hello, this is Archon. {body}"

    return raw


def _is_say_exactly_goal(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    return bool(
        re.search(
            r"^(?:please\s+)?(?:call\s+\w+\s+and\s+)?say\s+exactly\s+",
            raw,
            flags=re.IGNORECASE,
        )
    )


def _mission_is_interactive(mission: VoiceMission | None) -> bool:
    if mission is None:
        return False
    raw = str(mission.goal or "").strip()
    if not raw:
        return False
    if _is_say_exactly_goal(raw):
        return False
    return bool(re.search(r"\b(ask|chat|discuss|talk|conversation|converse)\b", raw, flags=re.IGNORECASE))


def _mission_script_text(mission: VoiceMission) -> str:
    text = (mission.goal or "").strip()
    if text:
        return _normalize_goal_for_speech(text)
    return "Hello. This is Archon calling."


def _append_transcript(mission: VoiceMission, speaker: str, text: str) -> None:
    value = str(text or "").strip()
    if not value:
        return
    mission.transcript.append(
        {
            "speaker": str(speaker or ""),
            "text": value,
            "timestamp": time.time(),
        }
    )
    mission.transcript = mission.transcript[-20:]


def _append_realtime_transcript_from_event(mission: VoiceMission, message: dict) -> None:
    if not isinstance(message, dict):
        return
    if str(message.get("type") or "") != "ConversationText":
        return
    text = str(message.get("content") or message.get("text") or "").strip()
    if not text:
        return
    role = str(message.get("role") or "").strip().lower()
    speaker = role or "assistant"
    if speaker == "agent":
        speaker = "assistant"
    _append_transcript(mission, speaker, text)


def _followup_question_for_goal(mission: VoiceMission) -> str:
    goal = str(mission.goal or "").lower()
    if "trading" in goal:
        return "What was your best trade today?"
    if "meeting" in goal:
        return "How did the meeting go?"
    return "Tell me a little more."


def _gather_no_input_prompt(mission: VoiceMission) -> str:
    if "trading" in str(mission.goal or "").lower():
        return "I didn't catch that. How is your trading going today?"
    return "I didn't catch that. Could you repeat that?"


def _gather_followup_prompt(mission: VoiceMission, user_speech: str) -> str:
    if user_speech:
        _append_transcript(mission, "user", user_speech)
        return f"Thanks for sharing. {_followup_question_for_goal(mission)}"
    return _gather_no_input_prompt(mission)


def _final_prompt(mission: VoiceMission, user_speech: str) -> str:
    if user_speech:
        _append_transcript(mission, "user", user_speech)
        if "trading" in str(mission.goal or "").lower():
            return "Thanks for the update on your trading. Good luck with the rest of your day. Goodbye."
        return "Thanks for the update. Goodbye."
    return "Thanks for your time. Goodbye."


async def _callback_payload(request: Request) -> dict:
    content_type = str(request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        try:
            payload = await request.json()
            if isinstance(payload, dict):
                return payload
        except Exception:
            return {}

    try:
        raw = await request.body()
    except Exception:
        return {}
    if not raw:
        return {}

    try:
        return {str(k): str(v) for k, v in parse_qsl(raw.decode("utf-8"), keep_blank_values=True)}
    except Exception:
        return {}


async def _callback_signature_params(request: Request) -> dict:
    content_type = str(request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        return await _callback_payload(request)

    try:
        raw = await request.body()
    except Exception:
        return {}
    if not raw:
        return {}

    try:
        pairs = parse_qsl(raw.decode("utf-8"), keep_blank_values=True)
    except Exception:
        return {}

    params: dict[str, str | list[str]] = {}
    for key, value in pairs:
        k = str(key)
        v = str(value)
        existing = params.get(k)
        if existing is None:
            params[k] = v
            continue
        if isinstance(existing, list):
            existing.append(v)
            continue
        params[k] = [existing, v]
    return params


def _require_valid_twilio_http_signature(request: Request, params: dict) -> None:
    if not _strict_twilio_signature_enabled():
        return
    if verify_twilio_signature(
        str(request.url),
        params,
        request.headers.get("x-twilio-signature"),
        auth_token=_env("TWILIO_AUTH_TOKEN"),
    ):
        return
    raise HTTPException(status_code=403, detail="invalid_twilio_signature")


async def _deepgram_audio_sink_stub(_chunk: bytes) -> None:
    """Placeholder sink until Task 7 wires a real Deepgram websocket client."""
    return None


def _create_deepgram_client(mission: VoiceMission) -> deepgram_agent.DeepgramVoiceAgentClient | None:
    """Create a mission-scoped Deepgram client when configured.

    Networking remains optional: tests can monkeypatch this factory, and local
    runs without `DEEPGRAM_API_KEY` keep the Task 6 skeleton behavior.
    """

    _ = mission
    api_key = _env("DEEPGRAM_API_KEY")
    if not api_key:
        return None
    return deepgram_agent.DeepgramVoiceAgentClient(api_key=api_key)


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "status": "healthy", "service": "archon_voice"}


@app.websocket("/twilio/missions/{mission_id}/stream")
async def mission_twilio_stream(mission_id: str, websocket: WebSocket) -> None:
    mission_key = str(mission_id)
    mission = _MISSIONS.get(mission_key)
    if mission is None:
        await websocket.close(code=1008, reason="unknown mission")
        _LOG.warning("twilio stream rejected: unknown mission_id=%s", mission_key)
        return

    bridge = RealtimeBridge(deepgram_audio_sink=_deepgram_audio_sink_stub)
    runtime = {
        "bridge": bridge,
        "deepgram_connected": False,
        "deepgram_client": None,
    }
    _MISSION_RUNTIMES[mission_key] = runtime
    mission.status = "in_progress"
    if mission.mode == "realtime_media_stream":
        think_provider, think_model = _deepgram_think_debug_config()
        mission.think_provider = think_provider
        mission.think_model = think_model
    _MISSIONS[mission.mission_id] = mission

    deepgram_client: object | None = None
    deepgram_receive_task: asyncio.Task | None = None
    deepgram_reconnect_attempts = 0
    twilio_stop_seen = False
    hangup_requested = False
    hangup_deadline = 0.0
    websocket_send_lock = asyncio.Lock()

    async def _send_twilio_json(message: dict) -> None:
        async with websocket_send_lock:
            await websocket.send_json(message)

    async def _cancel_deepgram_receive_task() -> None:
        nonlocal deepgram_receive_task
        task = deepgram_receive_task
        deepgram_receive_task = None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    async def _start_deepgram_client(candidate: object) -> None:
        nonlocal deepgram_client, deepgram_receive_task
        deepgram_client = candidate
        runtime["deepgram_client"] = candidate
        bridge.deepgram_audio_sink = deepgram_client.send_audio  # type: ignore[attr-defined]
        connect_timeout = _deepgram_connect_timeout_seconds()
        _LOG.info(
            "connecting deepgram client mission_id=%s timeout=%.2fs",
            mission_key,
            connect_timeout,
        )
        await asyncio.wait_for(
            deepgram_client.connect_and_initialize(goal=mission.goal),  # type: ignore[attr-defined]
            timeout=connect_timeout,
        )
        runtime["deepgram_connected"] = True
        mission.voice_backend = mission.voice_backend or "deepgram_voice_agent_v1"
        _MISSIONS[mission.mission_id] = mission
        await _cancel_deepgram_receive_task()
        deepgram_receive_task = asyncio.create_task(_deepgram_receive_loop())

    async def _try_reconnect_deepgram(reason: str) -> bool:
        nonlocal deepgram_client, deepgram_reconnect_attempts
        max_attempts = _deepgram_reconnect_max_attempts()
        if deepgram_reconnect_attempts >= max_attempts:
            return False
        deepgram_reconnect_attempts += 1
        _LOG.warning(
            "deepgram reconnect requested mission_id=%s reason=%s attempt=%s/%s",
            mission_key,
            reason,
            deepgram_reconnect_attempts,
            max_attempts,
            exc_info=True,
        )
        await _cancel_deepgram_receive_task()
        if deepgram_client is not None:
            with contextlib.suppress(Exception):
                await deepgram_client.close()  # type: ignore[attr-defined]
        deepgram_client = None
        runtime["deepgram_connected"] = False
        bridge.deepgram_audio_sink = _deepgram_audio_sink_stub

        candidate = _create_deepgram_client(mission)
        runtime["deepgram_client"] = candidate
        if candidate is None:
            return False
        try:
            await _start_deepgram_client(candidate)
        except asyncio.TimeoutError:
            _LOG.warning("deepgram reconnect timeout mission_id=%s", mission_key, exc_info=True)
            return False
        except Exception:
            _LOG.warning("deepgram reconnect failed mission_id=%s", mission_key, exc_info=True)
            return False
        _LOG.info("deepgram reconnect succeeded mission_id=%s", mission_key)
        return True

    async def _deepgram_receive_loop() -> None:
        nonlocal hangup_requested, hangup_deadline
        if deepgram_client is None:
            return
        try:
            while True:
                incoming = await deepgram_client.receive()  # type: ignore[attr-defined]
                if incoming is None:
                    return
                if isinstance(incoming, (bytes, bytearray)):
                    await bridge.relay_deepgram_audio_chunk_to_twilio(bytes(incoming), _send_twilio_json)
                    continue
                if isinstance(incoming, dict):
                    outbound = bridge.handle_deepgram_event(incoming)
                    _append_realtime_transcript_from_event(mission, incoming)
                    _MISSIONS[mission.mission_id] = mission
                    for message in outbound:
                        await _send_twilio_json(message)
                    if bridge.conversation_ended and not hangup_requested:
                        hangup_requested = True
                        hangup_deadline = time.monotonic() + 2.0
                        _LOG.info(
                            "assistant farewell detected; requesting hangup mission_id=%s",
                            mission_key,
                        )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Normal shutdown can race with a final send from this background task.
            _LOG.debug(
                "deepgram receive loop exiting after exception mission_id=%s",
                mission_key,
                exc_info=True,
            )
            return

    try:
        _LOG.info("twilio stream accepted mission_id=%s", mission_key)
        await websocket.accept()
        while True:
            if hangup_requested:
                try:
                    message = await asyncio.wait_for(websocket.receive_json(), timeout=0.2)
                except asyncio.TimeoutError:
                    message = None
            else:
                message = await websocket.receive_json()
            if not isinstance(message, dict):
                event_name = ""
            else:
                event_name = str(message.get("event") or "")
                try:
                    outbound = await bridge.handle_twilio_event_dict(message)
                except Exception:
                    if event_name == "media" and await _try_reconnect_deepgram("send_audio_failed"):
                        continue
                    raise
                for response_message in outbound:
                    await _send_twilio_json(response_message)

                if event_name == "start":
                    start = message.get("start")
                    if isinstance(start, dict):
                        call_sid = str(start.get("callSid") or "").strip()
                        if call_sid:
                            mission.provider_call_sid = call_sid
                        stream_sid = str(start.get("streamSid") or message.get("streamSid") or "").strip()
                        if stream_sid:
                            mission.twilio_stream_sid = stream_sid
                    elif str(message.get("streamSid") or "").strip():
                        mission.twilio_stream_sid = str(message.get("streamSid") or "").strip()
                    mission.provider = mission.provider or "twilio"
                    if not float(mission.realtime_session_started_at or 0.0):
                        mission.realtime_session_started_at = time.time()
                    _MISSIONS[mission.mission_id] = mission

                    if deepgram_client is None:
                        candidate = _create_deepgram_client(mission)
                        runtime["deepgram_client"] = candidate
                        if candidate is not None:
                            try:
                                await _start_deepgram_client(candidate)
                            except asyncio.TimeoutError:
                                _LOG.warning(
                                    "deepgram connect timeout mission_id=%s timeout=%.2fs",
                                    mission_key,
                                    _deepgram_connect_timeout_seconds(),
                                )
                                raise

                if event_name == "stop":
                    twilio_stop_seen = True
                    mission.status = "completed"
                    _MISSIONS[mission.mission_id] = mission
                    break

            if hangup_requested and (
                not bridge.agent_audio_in_flight or time.monotonic() >= hangup_deadline
            ):
                twilio_stop_seen = True
                mission.status = "completed"
                _MISSIONS[mission.mission_id] = mission
                _LOG.info(
                    "closing stream after assistant farewell mission_id=%s in_flight=%s",
                    mission_key,
                    bridge.agent_audio_in_flight,
                )
                break
    except WebSocketDisconnect:
        _LOG.info("twilio stream disconnected mission_id=%s", mission_key)
    except Exception:
        _LOG.exception("twilio stream handler failed mission_id=%s", mission_key)
    finally:
        await _cancel_deepgram_receive_task()

        if deepgram_client is not None:
            runtime["deepgram_connected"] = False
            with contextlib.suppress(Exception):
                await deepgram_client.close()  # type: ignore[attr-defined]

        _MISSION_RUNTIMES.pop(mission_key, None)
        latest = _MISSIONS.get(mission_key)
        if latest is not None:
            if float(latest.realtime_session_started_at or 0.0):
                latest.realtime_session_ended_at = time.time()
            if twilio_stop_seen:
                if str(latest.status or "") == "in_progress":
                    latest.status = "completed"
                _MISSIONS[latest.mission_id] = latest
            elif str(latest.status or "") == "in_progress":
                latest.status = "stream_disconnected"
                _MISSIONS[latest.mission_id] = latest
        _LOG.info("twilio stream cleanup complete mission_id=%s", mission_key)


@app.post("/missions")
async def create_mission(payload: dict) -> dict:
    mission = VoiceMission.from_dict(payload if isinstance(payload, dict) else {})
    if not mission.mission_id:
        return {
            "ok": False,
            "status": "error",
            "reason": "mission_id or call_session_id is required",
        }
    if not mission.target_number:
        return {
            "ok": False,
            "status": "error",
            "reason": "target_number is required",
        }
    if not mission.status:
        mission.status = "queued"
    mission.twiml_url = _mission_twiml_url(mission.mission_id)
    if mission.mode == "realtime_media_stream":
        think_provider, think_model = _deepgram_think_debug_config()
        mission.think_provider = think_provider
        mission.think_model = think_model
    _MISSIONS[mission.mission_id] = mission

    if _twilio_enabled():
        try:
            result = create_outbound_call(
                account_sid=_env("TWILIO_ACCOUNT_SID"),
                auth_token=_env("TWILIO_AUTH_TOKEN"),
                from_number=_env("TWILIO_FROM_NUMBER"),
                to_number=mission.target_number,
                twiml_url=mission.twiml_url,
                status_callback_url=_status_callback_url(mission.mission_id),
            )
            mission.provider_call_sid = str(result.get("sid") or "")
            mission.status = "initiated"
            _MISSIONS[mission.mission_id] = mission
        except Exception as e:
            return {
                "ok": False,
                "status": "error",
                "reason": f"twilio_call_failed: {type(e).__name__}: {e}",
                "mission": mission.to_dict(),
            }

    return {"ok": True, "mission": mission.to_dict(), "status": mission.status}


@app.get("/missions/{mission_id}")
async def get_mission(mission_id: str) -> dict:
    mission = _MISSIONS.get(str(mission_id))
    if mission is None:
        return {"ok": False, "status": "not_found", "mission_id": str(mission_id)}
    return {"ok": True, "mission": mission.to_dict(), "status": mission.status}


@app.post("/twilio/missions/{mission_id}/twiml")
async def mission_twiml(mission_id: str) -> Response:
    mission = _MISSIONS.get(str(mission_id))
    text = _mission_script_text(mission) if mission is not None else "Hello. This is Archon."
    if mission is not None and str(mission.mode or "") == "realtime_media_stream":
        mission.status = "in_progress"
        _MISSIONS[mission.mission_id] = mission
        xml = build_realtime_stream_twiml(_mission_stream_ws_url(mission_id))
    elif _mission_is_interactive(mission):
        mission.status = "in_progress"
        _MISSIONS[mission.mission_id] = mission
        xml = build_gather_twiml(
            text,
            action_url=_mission_gather_url(mission_id),
            voice="alice",
        )
    else:
        xml = build_say_twiml(text, voice="alice")
    return Response(content=xml, media_type="text/xml")


@app.post("/twilio/missions/{mission_id}/gather")
async def mission_gather(mission_id: str, request: Request) -> Response:
    mission = _MISSIONS.get(str(mission_id))
    if mission is None:
        xml = build_say_twiml("This call session is no longer available. Goodbye.", voice="alice")
        return Response(content=xml, media_type="text/xml")

    data = await _callback_payload(request)
    if sid := str(data.get("CallSid") or data.get("call_sid") or "").strip():
        mission.provider_call_sid = sid

    user_speech = str(data.get("SpeechResult") or data.get("speech_result") or "").strip()
    mission.turn_count = int(mission.turn_count or 0) + 1
    max_turns = max(1, int(mission.max_turns or 2))

    if mission.turn_count >= max_turns:
        mission.status = "completed"
        xml = build_say_twiml(_final_prompt(mission, user_speech), voice="alice")
    else:
        mission.status = "in_progress"
        xml = build_gather_twiml(
            _gather_followup_prompt(mission, user_speech),
            action_url=_mission_gather_url(mission_id),
            voice="alice",
        )

    _MISSIONS[mission.mission_id] = mission
    return Response(content=xml, media_type="text/xml")


@app.post("/twilio/status/{mission_id}")
async def mission_status_callback(mission_id: str, request: Request) -> dict:
    mission = _MISSIONS.get(str(mission_id))
    if mission is None:
        return {"ok": False, "status": "not_found", "mission_id": str(mission_id)}
    signature_params = await _callback_signature_params(request)
    _require_valid_twilio_http_signature(request, signature_params)
    data = await _callback_payload(request)
    provider_status = str(data.get("CallStatus") or data.get("call_status") or "").strip().lower()
    if provider_status:
        mission.status = provider_status
        _MISSIONS[mission.mission_id] = mission
    if sid := str(data.get("CallSid") or data.get("call_sid") or "").strip():
        mission.provider_call_sid = sid
        _MISSIONS[mission.mission_id] = mission
    return {"ok": True, "status": mission.status, "mission": mission.to_dict()}
