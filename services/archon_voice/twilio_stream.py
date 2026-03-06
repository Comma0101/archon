"""Twilio Media Streams websocket message helpers.

This module keeps parsing/building strict enough for realtime bridge use while
remaining lightweight (stdlib only).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_SUPPORTED_EVENTS = {"connected", "start", "media", "mark", "dtmf", "stop"}


@dataclass(frozen=True)
class ConnectedData:
    protocol: str | None = None
    version: str | None = None


@dataclass(frozen=True)
class StartData:
    account_sid: str | None = None
    call_sid: str | None = None
    tracks: tuple[str, ...] = ()
    media_format: dict[str, Any] | None = None
    custom_parameters: dict[str, str] | None = None


@dataclass(frozen=True)
class MediaData:
    payload: str
    track: str | None = None
    chunk: int | None = None
    timestamp_ms: int | None = None


@dataclass(frozen=True)
class MarkData:
    name: str


@dataclass(frozen=True)
class DtmfData:
    digit: str
    track: str | None = None


@dataclass(frozen=True)
class StopData:
    account_sid: str | None = None
    call_sid: str | None = None


@dataclass(frozen=True)
class TwilioInboundEvent:
    event: str
    stream_sid: str | None = None
    sequence_number: int | None = None
    connected: ConnectedData | None = None
    start: StartData | None = None
    media: MediaData | None = None
    mark: MarkData | None = None
    dtmf: DtmfData | None = None
    stop: StopData | None = None


def parse_inbound_event(message: dict[str, Any]) -> TwilioInboundEvent:
    """Parse and validate a Twilio Media Streams inbound event dict."""
    if not isinstance(message, dict):
        raise TypeError("message must be a dict")

    event = _require_str(message, "event")
    if event not in _SUPPORTED_EVENTS:
        raise ValueError(f"unsupported Twilio event: {event!r}")

    sequence_number = _optional_int(message.get("sequenceNumber"), "sequenceNumber")

    if event == "connected":
        return TwilioInboundEvent(
            event=event,
            stream_sid=_optional_str(message.get("streamSid"), "streamSid"),
            sequence_number=sequence_number,
            connected=ConnectedData(
                protocol=_optional_str(message.get("protocol"), "protocol"),
                version=_optional_str(message.get("version"), "version"),
            ),
        )

    if event == "start":
        start = _require_dict(message, "start")
        stream_sid = _optional_str(message.get("streamSid"), "streamSid") or _optional_str(
            start.get("streamSid"), "start.streamSid"
        )
        if not stream_sid:
            raise ValueError("start event missing streamSid")

        tracks_raw = start.get("tracks")
        if tracks_raw is None:
            tracks: tuple[str, ...] = ()
        elif isinstance(tracks_raw, list):
            tracks = tuple(_coerce_track(item) for item in tracks_raw)
        else:
            raise TypeError("start.tracks must be a list when provided")

        media_format = start.get("mediaFormat")
        if media_format is not None:
            media_format = _validate_start_media_format(media_format)

        custom_parameters_raw = start.get("customParameters")
        if custom_parameters_raw is None:
            custom_parameters: dict[str, str] | None = None
        elif isinstance(custom_parameters_raw, dict):
            custom_parameters = {
                str(key): str(value) for key, value in custom_parameters_raw.items()
            }
        else:
            raise TypeError("start.customParameters must be a dict when provided")

        return TwilioInboundEvent(
            event=event,
            stream_sid=stream_sid,
            sequence_number=sequence_number,
            start=StartData(
                account_sid=_optional_str(start.get("accountSid"), "start.accountSid"),
                call_sid=_optional_str(start.get("callSid"), "start.callSid"),
                tracks=tracks,
                media_format=media_format,
                custom_parameters=custom_parameters,
            ),
        )

    if event == "media":
        media = _require_dict(message, "media")
        stream_sid = _require_str(message, "streamSid")
        return TwilioInboundEvent(
            event=event,
            stream_sid=stream_sid,
            sequence_number=sequence_number,
            media=MediaData(
                payload=_require_str(media, "payload", path="media.payload"),
                track=_optional_str(media.get("track"), "media.track"),
                chunk=_optional_int(media.get("chunk"), "media.chunk"),
                timestamp_ms=_optional_int(media.get("timestamp"), "media.timestamp"),
            ),
        )

    if event == "mark":
        mark = _require_dict(message, "mark")
        return TwilioInboundEvent(
            event=event,
            stream_sid=_require_str(message, "streamSid"),
            sequence_number=sequence_number,
            mark=MarkData(name=_require_str(mark, "name", path="mark.name")),
        )

    if event == "dtmf":
        dtmf = _require_dict(message, "dtmf")
        return TwilioInboundEvent(
            event=event,
            stream_sid=_require_str(message, "streamSid"),
            sequence_number=sequence_number,
            dtmf=DtmfData(
                digit=_require_str(dtmf, "digit", path="dtmf.digit"),
                track=_optional_str(dtmf.get("track"), "dtmf.track"),
            ),
        )

    # event == "stop"
    stop = _require_dict(message, "stop")
    stream_sid = _optional_str(message.get("streamSid"), "streamSid") or _optional_str(
        stop.get("streamSid"), "stop.streamSid"
    )
    if not stream_sid:
        raise ValueError("stop event missing streamSid")

    return TwilioInboundEvent(
        event=event,
        stream_sid=stream_sid,
        sequence_number=sequence_number,
        stop=StopData(
            account_sid=_optional_str(stop.get("accountSid"), "stop.accountSid"),
            call_sid=_optional_str(stop.get("callSid"), "stop.callSid"),
        ),
    )


def build_media_message(*, stream_sid: str, payload: str) -> dict[str, Any]:
    """Build an outbound Twilio Media Streams media message."""
    return {
        "event": "media",
        "streamSid": _validate_outbound_str(stream_sid, "stream_sid"),
        "media": {
            "payload": _validate_outbound_str(payload, "payload"),
        },
    }


def build_mark_message(*, stream_sid: str, name: str) -> dict[str, Any]:
    """Build an outbound Twilio Media Streams mark message."""
    return {
        "event": "mark",
        "streamSid": _validate_outbound_str(stream_sid, "stream_sid"),
        "mark": {
            "name": _validate_outbound_str(name, "name"),
        },
    }


def build_clear_message(*, stream_sid: str) -> dict[str, Any]:
    """Build an outbound Twilio Media Streams clear message."""
    return {
        "event": "clear",
        "streamSid": _validate_outbound_str(stream_sid, "stream_sid"),
    }


def _require_dict(message: dict[str, Any], key: str) -> dict[str, Any]:
    value = message.get(key)
    if not isinstance(value, dict):
        raise TypeError(f"{key} must be a dict")
    return value


def _require_str(
    message: dict[str, Any], key: str, *, path: str | None = None
) -> str:
    value = message.get(key)
    label = path or key
    if not isinstance(value, str) or value == "":
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _optional_str(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string when provided")
    return value


def _optional_int(value: Any, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError(f"{label} must be an int-compatible value")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an int-compatible value") from exc


def _coerce_track(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError("start.tracks items must be strings")
    return value


def _validate_start_media_format(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("start.mediaFormat must be a dict when provided")

    if "encoding" not in value:
        raise ValueError("start.mediaFormat.encoding is required")
    encoding = value.get("encoding")
    if encoding != "audio/x-mulaw":
        raise ValueError("start.mediaFormat.encoding must be 'audio/x-mulaw'")

    if "sampleRate" not in value:
        raise ValueError("start.mediaFormat.sampleRate is required")
    sample_rate = _optional_int(value.get("sampleRate"), "start.mediaFormat.sampleRate")
    if sample_rate != 8000:
        raise ValueError("start.mediaFormat.sampleRate must be 8000")

    if "channels" not in value:
        raise ValueError("start.mediaFormat.channels is required")
    channels = _optional_int(value.get("channels"), "start.mediaFormat.channels")
    if channels != 1:
        raise ValueError("start.mediaFormat.channels must be 1")

    normalized = dict(value)
    normalized["encoding"] = encoding
    normalized["sampleRate"] = sample_rate
    normalized["channels"] = channels
    return normalized


def _validate_outbound_str(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or value == "":
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


__all__ = [
    "ConnectedData",
    "DtmfData",
    "MarkData",
    "MediaData",
    "StartData",
    "StopData",
    "TwilioInboundEvent",
    "build_clear_message",
    "build_mark_message",
    "build_media_message",
    "parse_inbound_event",
]
