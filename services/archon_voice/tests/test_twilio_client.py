"""Tests for stdlib Twilio Voice REST client helpers."""

import json


class _DummyResp:
    def __init__(self, payload: dict):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_create_outbound_call_posts_form(monkeypatch):
    seen: dict[str, object] = {}

    def fake_urlopen(req, timeout=10):
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        seen["timeout"] = timeout
        seen["content_type"] = req.headers.get("Content-type") or req.headers.get("Content-Type")
        seen["auth"] = req.headers.get("Authorization")
        seen["body"] = (req.data or b"").decode("utf-8")
        return _DummyResp({"sid": "CA1234567890", "status": "queued"})

    monkeypatch.setattr("services.archon_voice.twilio_client.urlrequest.urlopen", fake_urlopen)

    from services.archon_voice.twilio_client import create_outbound_call

    result = create_outbound_call(
        account_sid="AC123",
        auth_token="secret",
        from_number="+15550000000",
        to_number="+15551112222",
        twiml_url="https://example.com/twilio/missions/call_1/twiml",
    )

    assert result["sid"].startswith("CA")
    assert seen["url"].endswith("/2010-04-01/Accounts/AC123/Calls.json")
    assert seen["method"] == "POST"
    assert seen["timeout"] == 10
    assert "application/x-www-form-urlencoded" in str(seen["content_type"])
    assert str(seen["auth"]).startswith("Basic ")
    body = str(seen["body"])
    assert "To=%2B15551112222" in body
    assert "From=%2B15550000000" in body
    assert "Url=https%3A%2F%2Fexample.com%2Ftwilio%2Fmissions%2Fcall_1%2Ftwiml" in body

