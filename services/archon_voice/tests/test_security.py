"""Tests for Twilio request signature verification helpers."""

import base64
import hashlib
import hmac

from services.archon_voice.security import verify_twilio_signature


def _manual_twilio_signature(url: str, params: dict[str, str], auth_token: str) -> str:
    payload = url + "".join(f"{k}{v}" for k, v in sorted(params.items()))
    digest = hmac.new(auth_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def test_verify_twilio_signature_accepts_valid_signature():
    url = "https://example.com/twilio/status/call_1"
    params = {
        "CallSid": "CA123",
        "CallStatus": "completed",
    }
    auth_token = "secret"
    signature = _manual_twilio_signature(url, params, auth_token)

    assert verify_twilio_signature(url, params, signature, auth_token=auth_token) is True


def test_verify_twilio_signature_rejects_invalid_signature():
    url = "https://example.com/twilio/status/call_1"
    params = {
        "CallSid": "CA123",
        "CallStatus": "completed",
    }

    assert verify_twilio_signature(url, params, "bad", auth_token="secret") is False


def test_verify_twilio_signature_sorts_duplicate_keys_by_key_then_value():
    url = "https://example.com/twilio/status/call_1"
    params = {
        "CallSid": "CA123",
        "StatusCallbackEvent": ["initiated", "completed"],
        "CallStatus": "completed",
    }
    auth_token = "secret"
    ordered_pairs = [
        ("CallSid", "CA123"),
        ("CallStatus", "completed"),
        ("StatusCallbackEvent", "completed"),
        ("StatusCallbackEvent", "initiated"),
    ]
    payload = url + "".join(f"{k}{v}" for k, v in ordered_pairs)
    signature = base64.b64encode(
        hmac.new(auth_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1).digest()
    ).decode("ascii")

    assert verify_twilio_signature(url, params, signature, auth_token=auth_token) is True
