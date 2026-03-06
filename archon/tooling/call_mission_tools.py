"""Call mission tool registrations for Archon (Phase 1 scripted path)."""

from __future__ import annotations

import json

from archon.calls import runner as call_runner
from archon.safety import Level


def _format_result(payload: dict) -> str:
    if not isinstance(payload, dict):
        return str(payload)
    lines: list[str] = []
    for key in [
        "ok",
        "call_session_id",
        "status",
        "mode",
        "fallback_used",
        "target_number",
        "goal",
        "provider_call_sid",
        "think_provider",
        "think_model",
        "evaluation",
        "evaluation_summary",
        "transcript_summary",
        "reason",
        "count",
    ]:
        if key in payload:
            lines.append(f"{key}: {payload[key]}")
    findings_raw = payload.get("findings")
    if isinstance(findings_raw, str):
        try:
            findings_raw = json.loads(findings_raw)
        except Exception:
            findings_raw = None
    if isinstance(findings_raw, dict):
        lines.append("findings:")
        for key, value in findings_raw.items():
            lines.append(f"  {key}: {value}")
    missions = payload.get("missions")
    if isinstance(missions, list):
        for mission in missions:
            if not isinstance(mission, dict):
                continue
            sid = mission.get("call_session_id") or mission.get("mission_id") or ""
            status = mission.get("status") or ""
            target = mission.get("target_number") or ""
            lines.append(f"- {sid} [{status}] {target}".rstrip())
    voice_service = payload.get("voice_service")
    if isinstance(voice_service, dict):
        if "status" in voice_service:
            lines.append(f"voice_service_status: {voice_service.get('status')}")
        if voice_service.get("base_url"):
            lines.append(f"voice_service_base_url: {voice_service.get('base_url')}")
    return "\n".join(lines) if lines else str(payload)


def register_call_mission_tools(registry) -> None:
    def call_mission_start(target_number: str, goal: str, call_session_id: str = "") -> str:
        preview = " ".join(str(goal or "").split())
        if len(preview) > 120:
            preview = preview[:120] + "..."
        if not registry.confirmer(
            f"Start call mission to {target_number}: {preview}",
            Level.DANGEROUS,
        ):
            return "Call mission rejected by safety gate."
        payload = call_runner.start_call_mission(
            target_number=target_number,
            goal=goal,
            call_session_id=call_session_id,
        )
        return _format_result(payload)

    registry.register(
        "call_mission_start",
        "Start a voice call mission via the local Archon voice service (Phase 1 scripted Twilio path).",
        {
            "properties": {
                "target_number": {
                    "type": "string",
                    "description": "Destination phone number in E.164 format (e.g. +15551112222)",
                },
                "goal": {
                    "type": "string",
                    "description": "Call goal/script text for the Phase 1 scripted voice flow",
                },
                "call_session_id": {
                    "type": "string",
                    "description": "Optional explicit Archon call session ID",
                    "default": "",
                },
            },
            "required": ["target_number", "goal"],
        },
        call_mission_start,
    )

    def call_mission_status(call_session_id: str) -> str:
        return _format_result(call_runner.call_mission_status(call_session_id))

    registry.register(
        "call_mission_status",
        "Read stored state for a call mission by call_session_id.",
        {
            "properties": {
                "call_session_id": {
                    "type": "string",
                    "description": "Archon call mission session ID",
                },
            },
            "required": ["call_session_id"],
        },
        call_mission_status,
    )

    def call_mission_list(limit: int = 20) -> str:
        return _format_result(call_runner.list_call_missions(limit=int(limit)))

    registry.register(
        "call_mission_list",
        "List recent call missions stored by Archon.",
        {
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum missions to return",
                    "default": 20,
                },
            },
            "required": [],
        },
        call_mission_list,
    )

    def call_mission_cancel(call_session_id: str, reason: str = "Cancelled by user") -> str:
        if not registry.confirmer(f"Cancel call mission {call_session_id}", Level.DANGEROUS):
            return "Call mission cancel rejected by safety gate."
        return _format_result(call_runner.cancel_call_mission(call_session_id, reason=reason))

    registry.register(
        "call_mission_cancel",
        "Cancel a call mission in Archon local state (provider cancellation endpoint may be added later).",
        {
            "properties": {
                "call_session_id": {
                    "type": "string",
                    "description": "Archon call mission session ID",
                },
                "reason": {
                    "type": "string",
                    "description": "Optional cancellation reason",
                    "default": "Cancelled by user",
                },
            },
            "required": ["call_session_id"],
        },
        call_mission_cancel,
    )
