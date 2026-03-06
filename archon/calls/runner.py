"""Lightweight orchestration wrappers for local voice service calls."""

from __future__ import annotations

import json
import logging
import uuid

from archon.calls import service_client
from archon.calls.models import CallMission
from archon.calls.store import (
    append_call_event,
    list_call_missions as _list_stored_call_missions,
    load_call_mission as _load_stored_call_mission,
    load_call_mission_payload as _load_stored_call_mission_payload,
    save_call_mission,
    save_call_mission_payload as _save_stored_call_mission_payload,
)
from archon.config import Config, load_config
from archon.llm import LLMClient


_LOG = logging.getLogger("archon.calls.runner")
_EVAL_SYSTEM_PROMPT = (
    "You evaluate whether a phone call mission achieved its goal.\n"
    "Return JSON only with these keys:\n"
    "- evaluation: one of success, partial, failed\n"
    "- evaluation_summary: short factual explanation (<= 180 chars)\n"
    "- findings: dict of key facts extracted from the call (e.g. {\"store_hours\": \"9am-5pm\"}).\n"
    "  Use {} when the goal is action-oriented and there is no specific fact to extract.\n"
    "- transcript_summary: 2-3 sentence summary of what happened on the call\n"
    "Extract only facts explicitly present in the transcript.\n"
    "Do not include markdown or extra keys."
)
_EVAL_ALLOWED = {"success", "partial", "failed"}


def _active_config(config: Config | None = None) -> Config:
    return config if config is not None else load_config()


def _voice_service_base_url(config: Config) -> str:
    if not getattr(config, "calls", None):
        raise RuntimeError("calls config missing")
    if not config.calls.enabled:
        raise RuntimeError("calls.disabled")
    base_url = str(config.calls.voice_service.base_url or "").strip()
    if not base_url:
        raise RuntimeError("calls.voice_service.base_url missing")
    return base_url


def voice_service_health(config: Config | None = None) -> dict:
    cfg = _active_config(config)
    try:
        base_url = _voice_service_base_url(cfg)
    except Exception as e:
        return {"ok": False, "status": "disabled", "reason": str(e)}
    try:
        payload = service_client.voice_service_health(base_url=base_url)
    except Exception as e:
        return {
            "ok": False,
            "status": "unreachable",
            "reason": str(e),
            "base_url": base_url,
        }
    if "status" not in payload:
        payload["status"] = "healthy" if payload.get("ok") else "unknown"
    payload.setdefault("base_url", base_url)
    return payload


def submit_call_mission(
    mission_payload: dict,
    config: Config | None = None,
) -> dict:
    cfg = _active_config(config)
    try:
        base_url = _voice_service_base_url(cfg)
    except Exception as e:
        return {"ok": False, "status": "disabled", "reason": str(e)}
    if not isinstance(mission_payload, dict) or not mission_payload:
        return {"ok": False, "status": "error", "reason": "mission payload is required"}
    try:
        payload = service_client.submit_call_mission(
            base_url=base_url,
            mission_payload=mission_payload,
        )
    except Exception as e:
        return {
            "ok": False,
            "status": "error",
            "reason": str(e),
            "base_url": base_url,
        }
    payload.setdefault("base_url", base_url)
    return payload


def _valid_target_number(value: str) -> bool:
    text = str(value or "").strip()
    if not text.startswith("+"):
        return False
    digits = text[1:]
    return digits.isdigit() and len(digits) >= 7


def _new_call_session_id() -> str:
    return f"call_{uuid.uuid4().hex[:12]}"


def _realtime_preferred(config: Config) -> bool:
    calls_cfg = getattr(config, "calls", None)
    if not calls_cfg:
        return False
    realtime_cfg = getattr(calls_cfg, "realtime", None)
    if not realtime_cfg:
        return False
    if not bool(getattr(realtime_cfg, "enabled", False)):
        return False
    return bool(str(getattr(realtime_cfg, "provider", "") or "").strip())


def _submit_payload_for_mode(
    *,
    call_session_id: str,
    goal: str,
    target_number: str,
    mode: str,
) -> dict:
    return {
        "call_session_id": call_session_id,
        "goal": goal,
        "target_number": target_number,
        "mode": str(mode or "scripted_gather"),
    }


def _format_transcript_for_eval(transcript: object) -> str:
    if not isinstance(transcript, list):
        return ""
    lines: list[str] = []
    for item in transcript:
        if not isinstance(item, dict):
            continue
        speaker = str(item.get("speaker") or item.get("role") or "").strip().lower() or "unknown"
        text = str(item.get("text") or item.get("content") or "").strip()
        if not text:
            continue
        lines.append(f"{speaker}: {text}")
    return "\n".join(lines)


def _build_eval_user_message(goal: str, transcript_text: str) -> str:
    return (
        f"Mission goal:\n{goal.strip()}\n\n"
        f"Transcript:\n{transcript_text.strip()}\n\n"
        "Evaluate completion, extract any goal-relevant findings, and summarize the call. Respond with JSON only."
    )


def _extract_json_object(text: str) -> dict | None:
    source = str(text or "").strip()
    if not source:
        return None
    decoder = json.JSONDecoder()
    for idx, ch in enumerate(source):
        if ch != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(source[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _evaluate_call_outcome(goal: str, transcript_text: str, config: Config | None = None) -> dict | None:
    mission_goal = str(goal or "").strip()
    transcript_body = str(transcript_text or "").strip()
    if not mission_goal or not transcript_body:
        return None

    cfg = _active_config(config)
    client = LLMClient(
        provider=cfg.llm.provider,
        model=cfg.llm.model,
        api_key=cfg.llm.api_key,
        temperature=0.0,
        base_url=cfg.llm.base_url,
    )
    response = client.chat(
        _EVAL_SYSTEM_PROMPT,
        [{"role": "user", "content": _build_eval_user_message(mission_goal, transcript_body)}],
    )
    parsed = _extract_json_object(str(response.text or ""))
    if not isinstance(parsed, dict):
        return None

    evaluation = str(parsed.get("evaluation") or "").strip().lower()
    if evaluation not in _EVAL_ALLOWED:
        return None
    summary = str(parsed.get("evaluation_summary") or "").strip()
    if not summary:
        return None
    findings = _normalize_eval_findings(parsed.get("findings"))
    transcript_summary = str(parsed.get("transcript_summary") or "").strip()
    return {
        "evaluation": evaluation,
        "evaluation_summary": summary[:180],
        "findings": findings,
        "transcript_summary": transcript_summary[:500],
    }


def _normalize_eval_findings(raw: object) -> dict[str, str]:
    data = raw
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in data.items():
        k = str(key or "").strip()
        v = str(value or "").strip()
        if not k or not v:
            continue
        out[k] = v
    return out


def start_call_mission(
    *,
    target_number: str,
    goal: str,
    call_session_id: str = "",
    config: Config | None = None,
) -> dict:
    cfg = _active_config(config)
    preferred_mode = "realtime_media_stream" if _realtime_preferred(cfg) else "scripted_gather"
    number = str(target_number or "").strip()
    mission_goal = str(goal or "").strip()
    if not _valid_target_number(number):
        return {
            "ok": False,
            "status": "error",
            "reason": "invalid target_number (E.164 required)",
            "mode": preferred_mode,
            "fallback_used": False,
        }
    if not mission_goal:
        return {
            "ok": False,
            "status": "error",
            "reason": "goal is required",
            "mode": preferred_mode,
            "fallback_used": False,
        }

    health = voice_service_health(cfg)
    if not health.get("ok"):
        return {
            "ok": False,
            "status": "voice_service_unreachable",
            "reason": str(health.get("reason") or health.get("status") or "unreachable"),
            "voice_service": health,
            "mode": preferred_mode,
            "fallback_used": False,
        }

    sid = str(call_session_id or "").strip() or _new_call_session_id()
    mission = CallMission(
        call_session_id=sid,
        goal=mission_goal,
        target_number=number,
        status="queued",
        mode=preferred_mode,
    )
    save_call_mission(mission)
    append_call_event(sid, {"kind": "mission.created", "status": mission.status})

    fallback_used = False
    selected_mode = preferred_mode
    submit_result = submit_call_mission(
        _submit_payload_for_mode(
            call_session_id=sid,
            goal=mission_goal,
            target_number=number,
            mode=selected_mode,
        ),
        config=cfg,
    )
    if preferred_mode == "realtime_media_stream" and not submit_result.get("ok"):
        fallback_used = True
        selected_mode = "scripted_gather"
        mission.mode = selected_mode
        save_call_mission(mission)
        append_call_event(
            sid,
            {
                "kind": "mission.realtime_fallback",
                "status": mission.status,
                "reason": str(submit_result.get("reason") or submit_result.get("status") or "realtime submit failed"),
            },
        )
        submit_result = submit_call_mission(
            _submit_payload_for_mode(
                call_session_id=sid,
                goal=mission_goal,
                target_number=number,
                mode=selected_mode,
            ),
            config=cfg,
        )
    if not submit_result.get("ok"):
        mission.status = "error"
        mission.error = str(submit_result.get("reason") or "voice service mission submit failed")
        mission.mode = selected_mode
        save_call_mission(mission)
        append_call_event(
            sid,
            {"kind": "mission.submit_failed", "status": mission.status, "reason": mission.error},
        )
        return {
            "ok": False,
            "call_session_id": sid,
            "status": mission.status,
            "reason": mission.error,
            "voice_service": submit_result,
            "mode": mission.mode or selected_mode,
            "fallback_used": fallback_used,
        }

    service_mission = submit_result.get("mission")
    if isinstance(service_mission, dict):
        provider_sid = str(service_mission.get("provider_call_sid") or "")
        if provider_sid:
            mission.provider_call_sid = provider_sid
        service_mode = str(service_mission.get("mode") or "").strip()
        if service_mode:
            mission.mode = service_mode
    if not str(mission.mode or "").strip():
        mission.mode = selected_mode
    mission.status = str(submit_result.get("status") or mission.status or "queued")
    mission.error = ""
    save_call_mission(mission)
    append_call_event(sid, {"kind": "mission.submitted", "status": mission.status})
    return {
        "ok": True,
        "call_session_id": sid,
        "status": mission.status,
        "target_number": mission.target_number,
        "goal": mission.goal,
        "provider_call_sid": mission.provider_call_sid,
        "mode": mission.mode,
        "fallback_used": fallback_used,
        "voice_service": submit_result,
    }


def call_mission_status(call_session_id: str) -> dict:
    sid = str(call_session_id or "").strip()
    if not sid:
        return {"ok": False, "status": "error", "reason": "call_session_id is required"}
    mission = _load_stored_call_mission(sid)
    if mission is None:
        return {"ok": False, "status": "not_found", "call_session_id": sid}

    local_payload = mission.to_dict()
    stored_payload = _load_stored_call_mission_payload(sid) or dict(local_payload)
    if not isinstance(stored_payload, dict):
        stored_payload = dict(local_payload)

    cfg: Config | None = None
    try:
        cfg = _active_config()
        base_url = _voice_service_base_url(cfg)
        service_status = service_client.get_call_mission_status(
            base_url=base_url,
            call_session_id=sid,
        )
    except Exception:
        service_status = None

    if isinstance(service_status, dict) and service_status.get("ok"):
        service_mission = service_status.get("mission")
        if isinstance(service_mission, dict):
            merged_payload = dict(stored_payload)
            merged_payload.update(service_mission)
            merged_payload["call_session_id"] = sid
            merged_payload["status"] = str(
                service_status.get("status")
                or service_mission.get("status")
                or merged_payload.get("status")
                or local_payload.get("status")
                or ""
            )
            stored_payload = _save_stored_call_mission_payload(sid, merged_payload)

    result_payload = dict(stored_payload)
    result_payload.setdefault("call_session_id", sid)
    result_payload.setdefault("status", local_payload.get("status") or "")

    evaluation_value = str(result_payload.get("evaluation") or "").strip()
    needs_eval_enrichment = not evaluation_value
    if not needs_eval_enrichment:
        if "findings" not in result_payload:
            needs_eval_enrichment = True
        elif "transcript_summary" not in result_payload:
            needs_eval_enrichment = True
        elif not str(result_payload.get("transcript_summary") or "").strip():
            needs_eval_enrichment = True
    should_evaluate = (
        str(result_payload.get("status") or "").strip().lower() == "completed"
        and needs_eval_enrichment
    )
    if should_evaluate:
        transcript_text = _format_transcript_for_eval(result_payload.get("transcript"))
        goal_text = str(result_payload.get("goal") or local_payload.get("goal") or "").strip()
        if transcript_text and goal_text:
            try:
                evaluation_payload = _evaluate_call_outcome(goal_text, transcript_text, config=cfg)
            except Exception:
                _LOG.debug("call outcome evaluation failed sid=%s", sid, exc_info=True)
                evaluation_payload = None
            if isinstance(evaluation_payload, dict):
                evaluation = str(evaluation_payload.get("evaluation") or "").strip().lower()
                summary = str(evaluation_payload.get("evaluation_summary") or "").strip()
                if evaluation in _EVAL_ALLOWED and summary:
                    findings = _normalize_eval_findings(evaluation_payload.get("findings"))
                    transcript_summary = str(evaluation_payload.get("transcript_summary") or "").strip()
                    persisted = _save_stored_call_mission_payload(
                        sid,
                        {
                            **result_payload,
                            "evaluation": evaluation,
                            "evaluation_summary": summary,
                            "findings": findings,
                            "transcript_summary": transcript_summary[:500],
                        },
                    )
                    if isinstance(persisted, dict):
                        result_payload = dict(persisted)
                    else:
                        result_payload["evaluation"] = evaluation
                        result_payload["evaluation_summary"] = summary
                        result_payload["findings"] = findings
                        result_payload["transcript_summary"] = transcript_summary[:500]

    if not str(result_payload.get("evaluation") or "").strip():
        result_payload.pop("evaluation", None)
        result_payload.pop("evaluation_summary", None)
        result_payload.pop("findings", None)
        result_payload.pop("transcript_summary", None)
    else:
        if not str(result_payload.get("evaluation_summary") or "").strip():
            result_payload.pop("evaluation_summary", None)
        result_payload["findings"] = _normalize_eval_findings(result_payload.get("findings"))
        transcript_summary = str(result_payload.get("transcript_summary") or "").strip()
        if transcript_summary:
            result_payload["transcript_summary"] = transcript_summary[:500]
        else:
            result_payload.pop("transcript_summary", None)

    return {"ok": True, **result_payload}


def list_call_missions(limit: int = 20) -> dict:
    missions = _list_stored_call_missions(limit=limit)
    return {
        "ok": True,
        "count": len(missions),
        "missions": [m.to_dict() for m in missions],
    }


def cancel_call_mission(call_session_id: str, reason: str = "Cancelled by user") -> dict:
    sid = str(call_session_id or "").strip()
    if not sid:
        return {"ok": False, "status": "error", "reason": "call_session_id is required"}
    mission = _load_stored_call_mission(sid)
    if mission is None:
        return {"ok": False, "status": "not_found", "call_session_id": sid}
    mission.status = "cancelled"
    mission.error = str(reason or "Cancelled by user")
    save_call_mission(mission)
    append_call_event(sid, {"kind": "mission.cancelled", "status": mission.status, "reason": mission.error})
    return {
        "ok": True,
        "call_session_id": sid,
        "status": mission.status,
        "reason": mission.error,
        "cancelled_locally_only": True,
    }
