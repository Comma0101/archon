"""Stdlib Twilio Voice REST client helpers (no Twilio SDK dependency)."""

from __future__ import annotations

import base64
import json
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest


TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"


def create_outbound_call(
    *,
    account_sid: str,
    auth_token: str,
    from_number: str,
    to_number: str,
    twiml_url: str,
    status_callback_url: str = "",
    timeout: int = 10,
) -> dict:
    """Create an outbound Twilio call via the REST API."""
    sid = str(account_sid or "").strip()
    token = str(auth_token or "")
    from_num = str(from_number or "").strip()
    to_num = str(to_number or "").strip()
    url_value = str(twiml_url or "").strip()
    if not sid:
        raise ValueError("Twilio account_sid is required")
    if not token:
        raise ValueError("Twilio auth_token is required")
    if not from_num:
        raise ValueError("Twilio from_number is required")
    if not to_num:
        raise ValueError("Twilio to_number is required")
    if not url_value:
        raise ValueError("Twilio twiml_url is required")

    form = {
        "To": to_num,
        "From": from_num,
        "Url": url_value,
    }
    callback = str(status_callback_url or "").strip()
    if callback:
        form["StatusCallback"] = callback
        form["StatusCallbackMethod"] = "POST"

    body = urlparse.urlencode(form).encode("utf-8")
    basic = base64.b64encode(f"{sid}:{token}".encode("utf-8")).decode("ascii")
    req = urlrequest.Request(
        f"{TWILIO_API_BASE}/Accounts/{sid}/Calls.json",
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {basic}",
        },
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=int(timeout)) as resp:
            raw = resp.read()
    except urlerror.HTTPError as e:
        payload = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Twilio Calls API HTTP {e.code}: {payload}") from e
    except urlerror.URLError as e:
        raise RuntimeError(f"Twilio Calls API network error: {e.reason}") from e

    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise RuntimeError("Twilio Calls API returned invalid JSON") from e
    if not isinstance(data, dict):
        raise RuntimeError("Twilio Calls API returned non-object JSON")
    return data

