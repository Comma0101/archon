"""Tests for TwiML helper builders."""

import services.archon_voice.twiml as twiml
from services.archon_voice.twiml import build_gather_twiml, build_say_twiml


def test_build_say_twiml():
    xml = build_say_twiml("Hello", voice="alice")

    assert "<Say" in xml
    assert "Hello" in xml
    assert "<Response>" in xml


def test_build_gather_twiml():
    xml = build_gather_twiml(
        "How is your trading going today?",
        action_url="https://example.com/twilio/missions/call_1/gather",
        voice="alice",
    )

    assert "<Gather" in xml
    assert 'input="speech"' in xml
    assert 'action="https://example.com/twilio/missions/call_1/gather"' in xml
    assert "<Say" in xml
    assert "How is your trading going today?" in xml
    assert "<Hangup/>" in xml


def test_build_realtime_stream_twiml():
    xml = twiml.build_realtime_stream_twiml(
        "wss://example.com/twilio/missions/call_rt_1/stream"
    )

    assert "<Response>" in xml
    assert '<Pause length="1"/>' in xml
    assert "<Connect>" in xml
    assert "<Stream" in xml
    assert 'url="wss://example.com/twilio/missions/call_rt_1/stream"' in xml
    assert "<Hangup/>" in xml


def test_build_realtime_stream_twiml_omits_pause_when_zero_length():
    xml = twiml.build_realtime_stream_twiml(
        "wss://example.com/twilio/missions/call_rt_1/stream",
        pause_length=0,
    )

    assert '<Pause length="' not in xml
