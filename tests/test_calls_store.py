"""Tests for call mission models and persistence."""


class TestCallMissionStore:
    def test_call_mission_roundtrip(self):
        from archon.calls.models import CallMission

        mission = CallMission(
            call_session_id="call_123",
            goal="Call Comma and ask about their day",
            target_number="+15551234567",
            status="queued",
        )

        payload = mission.to_dict()
        restored = CallMission.from_dict(payload)

        assert restored.call_session_id == "call_123"
        assert restored.status == "queued"

    def test_call_mission_roundtrip_preserves_mode(self):
        from archon.calls.models import CallMission

        mission = CallMission(
            call_session_id="call_123",
            goal="Call Comma and ask about their day",
            target_number="+15551234567",
            status="queued",
            mode="realtime_media_stream",
        )

        payload = mission.to_dict()
        restored = CallMission.from_dict(payload)

        assert restored.mode == "realtime_media_stream"

    def test_call_mission_roundtrip_preserves_evaluation_fields(self):
        from archon.calls.models import CallMission

        mission = CallMission(
            call_session_id="call_123",
            goal="Call Comma and ask about their day",
            target_number="+15551234567",
            status="completed",
            evaluation="success",
            evaluation_summary="Goal achieved with clear confirmation.",
            findings={"store_hours": "9am-5pm"},
            transcript_summary="Agent confirmed the store hours and ended the call.",
        )

        payload = mission.to_dict()
        restored = CallMission.from_dict(payload)

        assert restored.evaluation == "success"
        assert "Goal achieved" in restored.evaluation_summary
        assert restored.findings == {"store_hours": "9am-5pm"}
        assert "store hours" in restored.transcript_summary

    def test_call_store_save_and_load(self, monkeypatch, tmp_path):
        from archon.calls.models import CallMission
        from archon.calls.store import load_call_mission, save_call_mission

        monkeypatch.setattr(
            "archon.calls.store.CALLS_MISSIONS_DIR",
            tmp_path / "archon" / "calls" / "missions",
        )
        monkeypatch.setattr(
            "archon.calls.store.CALLS_EVENTS_DIR",
            tmp_path / "archon" / "calls" / "events",
        )

        mission = CallMission(
            call_session_id="call_1",
            goal="x",
            target_number="+1555",
            status="queued",
        )

        save_call_mission(mission)
        loaded = load_call_mission("call_1")

        assert loaded is not None
        assert loaded.status == "queued"
        assert loaded.call_session_id == "call_1"
