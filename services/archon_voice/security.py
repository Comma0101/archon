"""Security helpers for Archon voice service integrations."""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Mapping
from typing import Any


def _twilio_signature_message(url: str, params: Mapping[str, Any] | None) -> str:
    parts = [str(url or "")]
    if params:
        normalized: list[tuple[str, str]] = []
        for raw_key, raw_value in params.items():
            key = str(raw_key)
            if isinstance(raw_value, (list, tuple)):
                for item in raw_value:
                    normalized.append((key, str(item)))
            else:
                normalized.append((key, str(raw_value)))
        normalized.sort(key=lambda item: (item[0], item[1]))
        for key, value in normalized:
            parts.append(key)
            parts.append(value)
    return "".join(parts)


def verify_twilio_signature(
    url: str,
    params: Mapping[str, Any] | None,
    signature: str | None,
    *,
    auth_token: str,
) -> bool:
    """Validate a Twilio request signature using stdlib HMAC-SHA1 rules."""

    if not str(url or "").strip():
        return False
    if not str(auth_token or ""):
        return False
    received = str(signature or "").strip()
    if not received:
        return False

    payload = _twilio_signature_message(url, params)
    digest = hmac.new(
        str(auth_token).encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    expected = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(expected, received)


__all__ = ["verify_twilio_signature"]
