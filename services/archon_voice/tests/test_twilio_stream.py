"""Tests for Twilio Media Streams websocket message helpers."""

from importlib import import_module

import pytest


def _twilio_stream_module():
    return import_module("services.archon_voice.twilio_stream")


def test_parse_inbound_media_message():
    twilio_stream = _twilio_stream_module()

    message = twilio_stream.parse_inbound_event(
        {
            "event": "media",
            "streamSid": "MZ123",
            "media": {
                "payload": "AQIDBA==",
                "track": "inbound",
                "chunk": "7",
                "timestamp": "160",
            },
        }
    )

    assert message.event == "media"
    assert message.stream_sid == "MZ123"
    assert message.media is not None
    assert message.media.payload == "AQIDBA=="


def test_parse_inbound_mark_message():
    twilio_stream = _twilio_stream_module()

    message = twilio_stream.parse_inbound_event(
        {
            "event": "mark",
            "streamSid": "MZ123",
            "mark": {"name": "assistant-turn-complete"},
        }
    )

    assert message.event == "mark"
    assert message.mark is not None
    assert message.mark.name == "assistant-turn-complete"


def test_build_outbound_media_message():
    twilio_stream = _twilio_stream_module()

    message = twilio_stream.build_media_message(
        stream_sid="MZ123",
        payload="AQIDBA==",
    )

    assert message == {
        "event": "media",
        "streamSid": "MZ123",
        "media": {"payload": "AQIDBA=="},
    }


def test_build_outbound_clear_message():
    twilio_stream = _twilio_stream_module()

    message = twilio_stream.build_clear_message(stream_sid="MZ123")

    assert message == {
        "event": "clear",
        "streamSid": "MZ123",
    }


def test_parse_inbound_start_message_with_valid_twilio_media_format():
    twilio_stream = _twilio_stream_module()

    message = twilio_stream.parse_inbound_event(
        {
            "event": "start",
            "streamSid": "MZ123",
            "start": {
                "accountSid": "AC123",
                "callSid": "CA123",
                "mediaFormat": {
                    "encoding": "audio/x-mulaw",
                    "sampleRate": 8000,
                    "channels": 1,
                },
            },
        }
    )

    assert message.event == "start"
    assert message.stream_sid == "MZ123"
    assert message.start is not None
    assert message.start.media_format == {
        "encoding": "audio/x-mulaw",
        "sampleRate": 8000,
        "channels": 1,
    }


def test_parse_inbound_start_message_rejects_invalid_media_format():
    twilio_stream = _twilio_stream_module()

    invalid_messages = [
        {
            "event": "start",
            "streamSid": "MZ123",
            "start": {
                "mediaFormat": {
                    "encoding": "audio/pcm",
                    "sampleRate": 8000,
                    "channels": 1,
                }
            },
        },
        {
            "event": "start",
            "streamSid": "MZ123",
            "start": {
                "mediaFormat": {
                    "encoding": "audio/x-mulaw",
                    "sampleRate": 16000,
                    "channels": 1,
                }
            },
        },
    ]

    for message in invalid_messages:
        with pytest.raises(ValueError, match="start\\.mediaFormat"):
            twilio_stream.parse_inbound_event(message)
