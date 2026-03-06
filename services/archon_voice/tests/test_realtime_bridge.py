"""Tests for realtime Twilio <-> Deepgram bridge state machine."""

from __future__ import annotations

import asyncio
from importlib import import_module

import pytest


def _realtime_bridge_module():
    return import_module("services.archon_voice.realtime_bridge")


class _AudioSink:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    async def send_audio(self, chunk: bytes) -> None:
        self.chunks.append(chunk)


def test_bridge_forwards_twilio_media_to_deepgram():
    realtime_bridge = _realtime_bridge_module()
    sink = _AudioSink()
    bridge = realtime_bridge.RealtimeBridge(deepgram_audio_sink=sink.send_audio)

    asyncio.run(
        bridge.handle_twilio_event_dict(
            {
                "event": "start",
                "streamSid": "MZ1",
                "start": {
                    "streamSid": "MZ1",
                },
            }
        )
    )
    asyncio.run(
        bridge.handle_twilio_event_dict(
            {
                "event": "media",
                "streamSid": "MZ1",
                "media": {
                    "payload": "YWJj",
                },
            }
        )
    )

    assert bridge.stream_sid == "MZ1"
    assert bridge.deepgram_audio_bytes_sent == 3
    assert sink.chunks == [b"abc"]


def test_bridge_emits_twilio_media_and_mark_from_deepgram_audio():
    realtime_bridge = _realtime_bridge_module()
    bridge = realtime_bridge.RealtimeBridge()

    asyncio.run(
        bridge.handle_twilio_event_dict(
            {
                "event": "start",
                "streamSid": "MZ1",
                "start": {
                    "streamSid": "MZ1",
                },
            }
        )
    )
    msgs = asyncio.run(bridge.handle_deepgram_audio_chunk(b"abc"))

    assert [m["event"] for m in msgs] == ["media", "mark"]
    assert msgs[0]["streamSid"] == "MZ1"
    assert msgs[0]["media"]["payload"] == "YWJj"
    assert msgs[1]["mark"]["name"]
    assert bridge.twilio_audio_bytes_queued == 3
    assert bridge.twilio_audio_bytes_sent == 3
    assert bridge.agent_audio_in_flight is True


def test_bridge_relay_deepgram_audio_chunk_sends_outbound_messages_via_json_sink():
    realtime_bridge = _realtime_bridge_module()
    bridge = realtime_bridge.RealtimeBridge()
    sent: list[dict] = []

    async def _send_json(message: dict) -> None:
        sent.append(message)

    asyncio.run(
        bridge.handle_twilio_event_dict(
            {
                "event": "start",
                "streamSid": "MZ1",
                "start": {
                    "streamSid": "MZ1",
                },
            }
        )
    )
    msgs = asyncio.run(bridge.relay_deepgram_audio_chunk_to_twilio(b"abc", _send_json))

    assert [m["event"] for m in msgs] == ["media", "mark"]
    assert [m["event"] for m in sent] == ["media", "mark"]
    assert sent[0]["media"]["payload"] == "YWJj"


def test_bridge_requests_clear_when_user_starts_speaking_mid_agent_audio():
    realtime_bridge = _realtime_bridge_module()
    bridge = realtime_bridge.RealtimeBridge()
    bridge.stream_sid = "MZ1"
    bridge.agent_audio_in_flight = True

    msgs = bridge.handle_deepgram_event({"type": "UserStartedSpeaking"})

    assert [m["event"] for m in msgs] == ["clear"]
    assert msgs[0]["streamSid"] == "MZ1"
    assert bridge.agent_audio_in_flight is False


def test_bridge_start_resets_per_call_state_and_sets_new_stream_sid():
    realtime_bridge = _realtime_bridge_module()
    bridge = realtime_bridge.RealtimeBridge()

    bridge.stream_sid = "OLD"
    bridge.deepgram_audio_bytes_sent = 7
    bridge.twilio_audio_bytes_queued = 8
    bridge.twilio_audio_bytes_sent = 9
    bridge.agent_audio_in_flight = True

    asyncio.run(
        bridge.handle_twilio_event_dict(
            {
                "event": "start",
                "streamSid": "MZ2",
                "start": {
                    "streamSid": "MZ2",
                },
            }
        )
    )

    assert bridge.stream_sid == "MZ2"
    assert bridge.agent_audio_in_flight is False
    assert bridge.deepgram_audio_bytes_sent == 0
    assert bridge.twilio_audio_bytes_queued == 0
    assert bridge.twilio_audio_bytes_sent == 0


def test_bridge_stop_clears_stream_sid_and_agent_audio_in_flight():
    realtime_bridge = _realtime_bridge_module()
    bridge = realtime_bridge.RealtimeBridge()
    bridge.stream_sid = "MZ1"
    bridge.agent_audio_in_flight = True

    asyncio.run(
        bridge.handle_twilio_event_dict(
            {
                "event": "stop",
                "streamSid": "MZ1",
                "stop": {
                    "streamSid": "MZ1",
                },
            }
        )
    )

    assert bridge.stream_sid is None
    assert bridge.agent_audio_in_flight is False


def test_bridge_deepgram_audio_chunk_raises_after_stop():
    realtime_bridge = _realtime_bridge_module()
    bridge = realtime_bridge.RealtimeBridge()

    asyncio.run(
        bridge.handle_twilio_event_dict(
            {
                "event": "start",
                "streamSid": "MZ1",
                "start": {
                    "streamSid": "MZ1",
                },
            }
        )
    )
    asyncio.run(
        bridge.handle_twilio_event_dict(
            {
                "event": "stop",
                "streamSid": "MZ1",
                "stop": {
                    "streamSid": "MZ1",
                },
            }
        )
    )

    with pytest.raises(ValueError, match="stream_sid is not set"):
        asyncio.run(bridge.handle_deepgram_audio_chunk(b"abc"))


def test_bridge_marks_conversation_ended_on_assistant_farewell():
    realtime_bridge = _realtime_bridge_module()
    bridge = realtime_bridge.RealtimeBridge()

    bridge.handle_deepgram_event(
        {
            "type": "ConversationText",
            "role": "assistant",
            "content": "Goodbye for now.",
        }
    )

    assert bridge.conversation_ended is True


def test_bridge_does_not_mark_conversation_ended_for_user_farewell():
    realtime_bridge = _realtime_bridge_module()
    bridge = realtime_bridge.RealtimeBridge()

    bridge.handle_deepgram_event(
        {
            "type": "ConversationText",
            "role": "user",
            "content": "goodbye",
        }
    )

    assert bridge.conversation_ended is False


def test_bridge_start_resets_conversation_ended_flag():
    realtime_bridge = _realtime_bridge_module()
    bridge = realtime_bridge.RealtimeBridge()
    bridge.conversation_ended = True

    asyncio.run(
        bridge.handle_twilio_event_dict(
            {
                "event": "start",
                "streamSid": "MZ2",
                "start": {
                    "streamSid": "MZ2",
                },
            }
        )
    )

    assert bridge.conversation_ended is False
