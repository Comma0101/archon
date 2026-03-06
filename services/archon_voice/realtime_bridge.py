"""Realtime Twilio <-> Deepgram bridge state machine.

This module is transport-agnostic: callers pass inbound event dicts/bytes and
receive outbound Twilio message dicts. No websocket runtime is included here.
"""

from __future__ import annotations

import base64
import inspect
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from . import deepgram_agent, twilio_stream


DeepgramAudioSink = Callable[[bytes], Awaitable[object] | object]
TwilioJsonSink = Callable[[dict[str, Any]], Awaitable[object] | object]
_FAREWELL_PATTERNS = (
    re.compile(r"\bgoodbye\b", re.IGNORECASE),
    re.compile(r"\bbye\b", re.IGNORECASE),
    re.compile(r"\bbye-bye\b", re.IGNORECASE),
    re.compile(r"\btake care\b", re.IGNORECASE),
    re.compile(r"\btalk to you (?:soon|later)\b", re.IGNORECASE),
)


@dataclass
class RealtimeBridge:
    """Minimal bridge state for Twilio Media Streams + Deepgram agent audio."""

    deepgram_audio_sink: DeepgramAudioSink | None = None

    stream_sid: str | None = None
    deepgram_audio_bytes_sent: int = 0
    twilio_audio_bytes_queued: int = 0
    twilio_audio_bytes_sent: int = 0
    agent_audio_in_flight: bool = False
    conversation_ended: bool = False

    captured_twilio_events: list[dict[str, Any]] = field(default_factory=list)
    captured_deepgram_events: list[dict[str, Any]] = field(default_factory=list)
    captured_transcripts: list[dict[str, str | None]] = field(default_factory=list)
    outbound_twilio_messages: list[dict[str, Any]] = field(default_factory=list)

    _mark_counter: int = 0

    async def handle_twilio_event_dict(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        """Consume a Twilio inbound event and optionally emit outbound messages."""
        event = twilio_stream.parse_inbound_event(message)
        self.captured_twilio_events.append(dict(message))

        if event.event == "start":
            self._reset_call_state()
            self.stream_sid = event.stream_sid
            return []

        if event.event == "media":
            if event.media is None:
                return []
            audio_chunk = base64.b64decode(event.media.payload)
            if self.deepgram_audio_sink is not None:
                result = self.deepgram_audio_sink(audio_chunk)
                if inspect.isawaitable(result):
                    await result
            self.deepgram_audio_bytes_sent += len(audio_chunk)
            return []

        if event.event == "mark":
            self.agent_audio_in_flight = False
            return []

        if event.event == "stop":
            self.agent_audio_in_flight = False
            self.stream_sid = None
            return []

        return []

    async def handle_deepgram_audio_chunk(self, chunk: bytes) -> list[dict[str, Any]]:
        """Convert Deepgram audio bytes into Twilio media + mark messages."""
        if self.stream_sid is None:
            raise ValueError("stream_sid is not set")
        if not isinstance(chunk, (bytes, bytearray)):
            raise TypeError("chunk must be bytes")

        chunk_bytes = bytes(chunk)
        payload = base64.b64encode(chunk_bytes).decode("ascii")
        media_message = twilio_stream.build_media_message(
            stream_sid=self.stream_sid,
            payload=payload,
        )
        mark_message = twilio_stream.build_mark_message(
            stream_sid=self.stream_sid,
            name=self._next_mark_name(),
        )
        messages = [media_message, mark_message]

        self.twilio_audio_bytes_queued += len(chunk_bytes)
        self.twilio_audio_bytes_sent += len(chunk_bytes)
        self.agent_audio_in_flight = len(chunk_bytes) > 0
        self.outbound_twilio_messages.extend(messages)
        return messages

    async def relay_deepgram_audio_chunk_to_twilio(
        self,
        chunk: bytes,
        twilio_json_sink: TwilioJsonSink,
    ) -> list[dict[str, Any]]:
        """Convert Deepgram audio bytes and immediately emit Twilio outbound JSON."""
        messages = await self.handle_deepgram_audio_chunk(chunk)
        for message in messages:
            result = twilio_json_sink(message)
            if inspect.isawaitable(result):
                await result
        return messages

    def handle_deepgram_event(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        """Consume a Deepgram JSON event and optionally emit Twilio control messages."""
        normalized = deepgram_agent.parse_deepgram_json_event(message)
        self.captured_deepgram_events.append(dict(message))

        if normalized.type == "ConversationText":
            self.captured_transcripts.append(
                {
                    "role": normalized.role,
                    "text": normalized.text,
                }
            )
            role = str(normalized.role or "").strip().lower()
            text = str(normalized.text or "").strip()
            if role in {"assistant", "agent"} and _is_agent_farewell(text):
                self.conversation_ended = True

        if (
            normalized.type == "UserStartedSpeaking"
            and self.agent_audio_in_flight
            and self.stream_sid is not None
        ):
            self.agent_audio_in_flight = False
            clear_message = twilio_stream.build_clear_message(stream_sid=self.stream_sid)
            self.outbound_twilio_messages.append(clear_message)
            return [clear_message]

        return []

    def _next_mark_name(self) -> str:
        self._mark_counter += 1
        return f"agent-audio-{self._mark_counter}"

    def _reset_call_state(self) -> None:
        """Reset stream-scoped mutable state for reused bridge instances."""
        self.deepgram_audio_bytes_sent = 0
        self.twilio_audio_bytes_queued = 0
        self.twilio_audio_bytes_sent = 0
        self.agent_audio_in_flight = False
        self.conversation_ended = False
        self._mark_counter = 0


def _is_agent_farewell(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    for pattern in _FAREWELL_PATTERNS:
        if pattern.search(value):
            return True
    return False


__all__ = ["RealtimeBridge"]
