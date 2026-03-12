"""Persistent storage for project setup jobs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from archon.config import STATE_DIR
from archon.control.jobs import JobSummary, job_summary_from_setup_record
from archon.setup.models import SetupRecord


SETUP_STATE_DIR = STATE_DIR / "setup"
SETUP_RECORDS_DIR = SETUP_STATE_DIR / "records"


def _ensure_dirs() -> None:
    SETUP_RECORDS_DIR.mkdir(parents=True, exist_ok=True)


def setup_record_path(setup_id: str) -> Path:
    safe_id = str(setup_id).replace("/", "_").replace("\\", "_").replace("..", "_")
    return SETUP_RECORDS_DIR / f"{safe_id}.json"


def _read_json_object(path: Path) -> dict[str, object] | None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def save_setup_record(record: SetupRecord) -> SetupRecord:
    _ensure_dirs()
    payload = record.to_dict()
    now = _now_iso()
    if not str(payload.get("created_at", "") or "").strip():
        payload["created_at"] = now
    payload["updated_at"] = now
    with open(setup_record_path(str(payload.get("setup_id", "") or "")), "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return SetupRecord.from_dict(payload)


def load_setup_record(setup_id: str) -> SetupRecord | None:
    path = setup_record_path(setup_id)
    if not path.exists():
        return None
    data = _read_json_object(path)
    if data is None:
        return None
    return SetupRecord.from_dict(data)


def list_setup_records(limit: int = 20) -> list[SetupRecord]:
    try:
        _ensure_dirs()
    except OSError:
        return []
    records: list[SetupRecord] = []
    files = sorted(SETUP_RECORDS_DIR.glob("*.json"), reverse=True)
    for path in files[: max(0, int(limit))]:
        data = _read_json_object(path)
        if data is None:
            continue
        records.append(SetupRecord.from_dict(data))
    records.sort(key=lambda record: (record.updated_at, record.setup_id), reverse=True)
    return records


def load_setup_job_summary(setup_id: str) -> JobSummary | None:
    record = load_setup_record(setup_id)
    if record is None:
        return None
    return job_summary_from_setup_record(record)


def list_setup_job_summaries(limit: int = 20) -> list[JobSummary]:
    return [job_summary_from_setup_record(record) for record in list_setup_records(limit=limit)]


def list_blocked_setup_records(limit: int = 50) -> list[SetupRecord]:
    return [
        record
        for record in list_setup_records(limit=limit)
        if str(record.status or "").strip().lower() == "blocked"
    ]
