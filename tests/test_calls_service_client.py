"""Tests for local voice-service HTTP client helpers."""

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


class TestCallsServiceClient:
    def test_voice_service_health_ok(self, monkeypatch):
        seen: dict[str, object] = {}

        def fake_urlopen(req, timeout=10):
            seen["url"] = req.full_url
            seen["method"] = req.get_method()
            seen["timeout"] = timeout
            return _DummyResp({"ok": True, "status": "healthy"})

        monkeypatch.setattr("archon.calls.service_client.urlrequest.urlopen", fake_urlopen)

        from archon.calls.service_client import voice_service_health

        result = voice_service_health(base_url="http://127.0.0.1:8788")

        assert result["ok"] is True
        assert seen["url"] == "http://127.0.0.1:8788/health"
        assert seen["method"] == "GET"
        assert seen["timeout"] == 5

    def test_submit_call_mission_posts_json(self, monkeypatch):
        seen: dict[str, object] = {}

        def fake_urlopen(req, timeout=10):
            seen["url"] = req.full_url
            seen["method"] = req.get_method()
            seen["timeout"] = timeout
            seen["content_type"] = req.headers.get("Content-type") or req.headers.get("Content-Type")
            seen["payload"] = json.loads((req.data or b"{}").decode("utf-8"))
            return _DummyResp({"ok": True, "mission": {"call_session_id": "call_1"}})

        monkeypatch.setattr("archon.calls.service_client.urlrequest.urlopen", fake_urlopen)

        from archon.calls.service_client import submit_call_mission

        result = submit_call_mission(
            base_url="http://127.0.0.1:8788",
            mission_payload={"call_session_id": "call_1", "goal": "Call me"},
        )

        assert result["ok"] is True
        assert seen["url"] == "http://127.0.0.1:8788/missions"
        assert seen["method"] == "POST"
        assert seen["timeout"] == 10
        assert "application/json" in str(seen["content_type"])
        assert seen["payload"]["call_session_id"] == "call_1"
        assert seen["payload"]["goal"] == "Call me"

    def test_get_call_mission_status_requests_mission_endpoint(self, monkeypatch):
        seen: dict[str, object] = {}

        def fake_urlopen(req, timeout=10):
            seen["url"] = req.full_url
            seen["method"] = req.get_method()
            seen["timeout"] = timeout
            return _DummyResp(
                {
                    "ok": True,
                    "status": "in_progress",
                    "mission": {
                        "mission_id": "call_1",
                        "mode": "realtime_media_stream",
                        "voice_backend": "deepgram_voice_agent_v1",
                    },
                }
            )

        monkeypatch.setattr("archon.calls.service_client.urlrequest.urlopen", fake_urlopen)

        from archon.calls.service_client import get_call_mission_status

        result = get_call_mission_status(
            base_url="http://127.0.0.1:8788",
            call_session_id="call_1",
            timeout=7,
        )

        assert result["ok"] is True
        assert result["status"] == "in_progress"
        assert result["mission"]["mode"] == "realtime_media_stream"
        assert seen["url"] == "http://127.0.0.1:8788/missions/call_1"
        assert seen["method"] == "GET"
        assert seen["timeout"] == 7
