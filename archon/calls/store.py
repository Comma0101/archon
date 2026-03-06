"""Persistent storage for call missions and event logs."""

from __future__ import annotations

import json
import time
from pathlib import Path

from archon.calls.models import CallMission
from archon.config import CALLS_EVENTS_DIR, CALLS_MISSIONS_DIR


_CALL_MISSION_BASE_FIELDS = {
    "call_session_id",
    "goal",
    "target_number",
    "status",
    "created_at",
    "updated_at",
    "provider_call_sid",
    "error",
    "mode",
    "evaluation",
    "evaluation_summary",
    "findings",
    "transcript_summary",
}


def _ensure_dirs() -> None:
    CALLS_MISSIONS_DIR.mkdir(parents=True, exist_ok=True)
    CALLS_EVENTS_DIR.mkdir(parents=True, exist_ok=True)


def call_mission_path(call_session_id: str) -> Path:
    return CALLS_MISSIONS_DIR / f"{call_session_id}.json"


def call_events_path(call_session_id: str) -> Path:
    return CALLS_EVENTS_DIR / f"{call_session_id}.jsonl"


def _read_json_object(path: Path) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def save_call_mission(mission: CallMission) -> dict:
    _ensure_dirs()
    now = time.time()
    if mission.created_at <= 0:
        mission.created_at = now
    mission.updated_at = now
    payload = mission.to_dict()
    path = call_mission_path(mission.call_session_id)
    existing = _read_json_object(path)
    if isinstance(existing, dict):
        for key, value in existing.items():
            if key not in _CALL_MISSION_BASE_FIELDS and key not in payload:
                payload[key] = value
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    return payload


def save_call_mission_payload(call_session_id: str, payload: dict) -> dict:
    """Persist a raw mission payload, preserving unknown/realtime fields."""
    if not isinstance(payload, dict):
        raise TypeError("payload must be a dict")
    _ensure_dirs()
    path = call_mission_path(call_session_id)
    existing = _read_json_object(path) or {}
    merged = dict(existing)
    merged.update(payload)
    merged["call_session_id"] = str(call_session_id)

    now = time.time()
    created_at = merged.get("created_at", now)
    try:
        merged["created_at"] = float(created_at)
    except (TypeError, ValueError):
        merged["created_at"] = now
    merged["updated_at"] = now

    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f)
    return merged


def load_call_mission_payload(call_session_id: str) -> dict | None:
    path = call_mission_path(call_session_id)
    if not path.exists():
        return None
    data = _read_json_object(path)
    if data is None:
        return None
    return dict(data)


def load_call_mission(call_session_id: str) -> CallMission | None:
    path = call_mission_path(call_session_id)
    if not path.exists():
        return None
    data = _read_json_object(path)
    if data is None:
        return None
    return CallMission.from_dict(data)


def list_call_missions(limit: int = 20) -> list[CallMission]:
    _ensure_dirs()
    missions: list[CallMission] = []
    files = sorted(CALLS_MISSIONS_DIR.glob("*.json"), reverse=True)
    for path in files[: max(0, int(limit))]:
        data = _read_json_object(path)
        if data is None:
            continue
        missions.append(CallMission.from_dict(data))
    return missions


def append_call_event(call_session_id: str, event: dict) -> dict:
    _ensure_dirs()
    entry = {
        "call_session_id": call_session_id,
        "timestamp": time.time(),
    }
    if isinstance(event, dict):
        entry.update(event)
    path = call_events_path(call_session_id)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry
