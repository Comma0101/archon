"""Local HTTP client helpers for the Archon voice service (stdlib only)."""

from __future__ import annotations

import json
from urllib import error as urlerror
from urllib import request as urlrequest


def _normalize_base_url(base_url: str) -> str:
    value = str(base_url or "").strip().rstrip("/")
    if not value:
        raise ValueError("voice service base_url is required")
    return value


def _request_json(
    *,
    method: str,
    url: str,
    payload: dict | None = None,
    timeout: int,
) -> dict:
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urlrequest.Request(url, data=data, headers=headers, method=method)
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urlerror.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"voice service HTTP {e.code}: {body}") from e
    except urlerror.URLError as e:
        raise RuntimeError(f"voice service network error: {e.reason}") from e
    except TimeoutError as e:
        raise RuntimeError("voice service request timed out") from e

    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise RuntimeError("voice service returned invalid JSON") from e
    if not isinstance(data, dict):
        raise RuntimeError("voice service returned non-object JSON")
    return data


def voice_service_health(base_url: str, timeout: int = 5) -> dict:
    """GET /health from the local voice service."""
    base = _normalize_base_url(base_url)
    return _request_json(method="GET", url=f"{base}/health", timeout=int(timeout))


def voice_service_start_mission(
    base_url: str,
    mission_payload: dict,
    timeout: int = 10,
) -> dict:
    """POST /missions to the local voice service."""
    base = _normalize_base_url(base_url)
    payload = mission_payload if isinstance(mission_payload, dict) else {}
    return _request_json(
        method="POST",
        url=f"{base}/missions",
        payload=payload,
        timeout=int(timeout),
    )


def voice_service_get_mission(
    base_url: str,
    call_session_id: str,
    timeout: int = 10,
) -> dict:
    """GET /missions/{id} from the local voice service."""
    base = _normalize_base_url(base_url)
    mission_id = str(call_session_id or "").strip()
    if not mission_id:
        raise ValueError("call_session_id is required")
    return _request_json(
        method="GET",
        url=f"{base}/missions/{mission_id}",
        timeout=int(timeout),
    )


def submit_call_mission(
    *,
    base_url: str,
    mission_payload: dict,
    timeout: int = 10,
) -> dict:
    """Compatibility helper used by call mission orchestration."""
    return voice_service_start_mission(
        base_url=base_url,
        mission_payload=mission_payload,
        timeout=timeout,
    )


def get_call_mission_status(
    *,
    base_url: str,
    call_session_id: str,
    timeout: int = 10,
) -> dict:
    """Compatibility helper for polling mission status from the voice service."""
    return voice_service_get_mission(
        base_url=base_url,
        call_session_id=call_session_id,
        timeout=timeout,
    )
