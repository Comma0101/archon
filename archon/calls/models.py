"""Data models for call mission control-plane state."""

import json
from dataclasses import dataclass, field


@dataclass
class CallVoiceServiceConfig:
    mode: str = "systemd"
    base_url: str = "http://127.0.0.1:8788"
    systemd_unit: str = "archon-voice.service"


@dataclass
class TwilioCallsConfig:
    account_sid: str = ""
    auth_token: str = ""
    from_number: str = ""
    status_callback_url: str = ""


@dataclass
class RealtimeCallsConfig:
    enabled: bool = False
    provider: str = "deepgram_voice_agent_v1"


@dataclass
class CallsConfig:
    enabled: bool = False
    voice_service: CallVoiceServiceConfig = field(default_factory=CallVoiceServiceConfig)
    realtime: RealtimeCallsConfig = field(default_factory=RealtimeCallsConfig)
    twilio: TwilioCallsConfig = field(default_factory=TwilioCallsConfig)


@dataclass
class CallMission:
    call_session_id: str
    goal: str
    target_number: str
    status: str
    created_at: float = 0.0
    updated_at: float = 0.0
    provider_call_sid: str = ""
    error: str = ""
    mode: str = "scripted_gather"
    evaluation: str = ""
    evaluation_summary: str = ""
    findings: dict[str, str] = field(default_factory=dict)
    transcript_summary: str = ""

    def to_dict(self) -> dict:
        return {
            "call_session_id": self.call_session_id,
            "goal": self.goal,
            "target_number": self.target_number,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "provider_call_sid": self.provider_call_sid,
            "error": self.error,
            "mode": self.mode,
            "evaluation": self.evaluation,
            "evaluation_summary": self.evaluation_summary,
            "findings": dict(self.findings),
            "transcript_summary": self.transcript_summary,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CallMission":
        raw_findings = data.get("findings", {})
        findings: dict[str, str]
        if isinstance(raw_findings, dict):
            findings = {
                str(k): str(v)
                for k, v in raw_findings.items()
                if str(k).strip() and str(v).strip()
            }
        elif isinstance(raw_findings, str):
            try:
                decoded = json.loads(raw_findings)
            except json.JSONDecodeError:
                decoded = {}
            if isinstance(decoded, dict):
                findings = {
                    str(k): str(v)
                    for k, v in decoded.items()
                    if str(k).strip() and str(v).strip()
                }
            else:
                findings = {}
        else:
            findings = {}
        return cls(
            call_session_id=str(data.get("call_session_id", "")),
            goal=str(data.get("goal", "")),
            target_number=str(data.get("target_number", "")),
            status=str(data.get("status", "")),
            created_at=float(data.get("created_at", 0) or 0),
            updated_at=float(data.get("updated_at", 0) or 0),
            provider_call_sid=str(data.get("provider_call_sid", "")),
            error=str(data.get("error", "")),
            mode=str(data.get("mode") or "scripted_gather"),
            evaluation=str(data.get("evaluation", "")),
            evaluation_summary=str(data.get("evaluation_summary", "")),
            findings=findings,
            transcript_summary=str(data.get("transcript_summary", "")),
        )
