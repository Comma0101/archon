"""Lightweight service-side models for mission placeholders."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class VoiceMission:
    mission_id: str
    status: str = "queued"
    goal: str = ""
    target_number: str = ""
    mode: str = "scripted_gather"
    provider: str = ""
    voice_backend: str = ""
    think_provider: str = ""
    think_model: str = ""
    provider_call_sid: str = ""
    twilio_stream_sid: str = ""
    realtime_session_started_at: float = 0.0
    realtime_session_ended_at: float = 0.0
    twiml_url: str = ""
    turn_count: int = 0
    max_turns: int = 2
    transcript: list[dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "mission_id": self.mission_id,
            "status": self.status,
            "goal": self.goal,
            "target_number": self.target_number,
            "mode": self.mode,
            "provider": self.provider,
            "voice_backend": self.voice_backend,
            "think_provider": self.think_provider,
            "think_model": self.think_model,
            "provider_call_sid": self.provider_call_sid,
            "twilio_stream_sid": self.twilio_stream_sid,
            "realtime_session_started_at": float(self.realtime_session_started_at or 0.0),
            "realtime_session_ended_at": float(self.realtime_session_ended_at or 0.0),
            "twiml_url": self.twiml_url,
            "turn_count": int(self.turn_count),
            "max_turns": int(self.max_turns),
            "transcript": list(self.transcript),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "VoiceMission":
        transcript = data.get("transcript", [])
        if not isinstance(transcript, list):
            transcript = []
        return cls(
            mission_id=str(data.get("mission_id") or data.get("call_session_id") or ""),
            status=str(data.get("status") or "queued"),
            goal=str(data.get("goal") or ""),
            target_number=str(data.get("target_number") or ""),
            mode=str(data.get("mode") or "scripted_gather"),
            provider=str(data.get("provider") or ""),
            voice_backend=str(data.get("voice_backend") or ""),
            think_provider=str(data.get("think_provider") or ""),
            think_model=str(data.get("think_model") or ""),
            provider_call_sid=str(data.get("provider_call_sid") or ""),
            twilio_stream_sid=str(data.get("twilio_stream_sid") or ""),
            realtime_session_started_at=float(data.get("realtime_session_started_at") or 0.0),
            realtime_session_ended_at=float(data.get("realtime_session_ended_at") or 0.0),
            twiml_url=str(data.get("twiml_url") or ""),
            turn_count=int(data.get("turn_count") or 0),
            max_turns=max(1, int(data.get("max_turns") or 2)),
            transcript=[
                _transcript_item_from_dict(item)
                for item in transcript
                if isinstance(item, dict)
            ],
        )


def _transcript_item_from_dict(item: dict) -> dict[str, object]:
    out: dict[str, object] = {
        "speaker": str(item.get("speaker") or ""),
        "text": str(item.get("text") or ""),
    }
    if "timestamp" in item:
        try:
            out["timestamp"] = float(item.get("timestamp") or 0.0)
        except (TypeError, ValueError):
            out["timestamp"] = 0.0
    return out
