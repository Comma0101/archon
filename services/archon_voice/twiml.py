"""Minimal TwiML XML helpers for Phase 0/1 scripted responses."""

from __future__ import annotations

from xml.sax.saxutils import escape as xml_escape


def _response_xml(body: str) -> str:
    return f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>'


def say(text: str, voice: str | None = None) -> str:
    attrs = ""
    if voice:
        attrs = f' voice="{xml_escape(str(voice))}"'
    return f"<Say{attrs}>{xml_escape(str(text))}</Say>"


def hangup() -> str:
    return "<Hangup/>"


def pause(length: int | float = 1) -> str:
    value = float(length)
    if value <= 0:
        return ""
    if value.is_integer():
        encoded = str(int(value))
    else:
        encoded = str(value)
    return f'<Pause length="{xml_escape(encoded)}"/>'


def gather(
    body: str,
    *,
    action_url: str,
    input_mode: str = "speech",
    method: str = "POST",
    speech_timeout: str = "auto",
) -> str:
    return (
        "<Gather"
        f' input="{xml_escape(str(input_mode))}"'
        f' action="{xml_escape(str(action_url))}"'
        f' method="{xml_escape(str(method))}"'
        f' speechTimeout="{xml_escape(str(speech_timeout))}"'
        ">"
        f"{body}"
        "</Gather>"
    )


def build_say_twiml(text: str, voice: str | None = None) -> str:
    return _response_xml(say(text, voice=voice) + hangup())


def build_gather_twiml(text: str, action_url: str, voice: str | None = None) -> str:
    return _response_xml(
        gather(
            say(text, voice=voice),
            action_url=action_url,
        )
        + hangup()
    )


def build_hangup_twiml() -> str:
    return _response_xml(hangup())


def build_realtime_stream_twiml(stream_url: str, *, pause_length: int | float = 1) -> str:
    stream = f'<Stream url="{xml_escape(str(stream_url))}"/>'
    return _response_xml(f"{pause(pause_length)}<Connect>{stream}</Connect>{hangup()}")
