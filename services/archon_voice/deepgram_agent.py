"""Deepgram Voice Agent protocol helpers and a minimal websocket runtime client."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
import inspect
import json
import os
import re
from typing import Any


DEEPGRAM_AGENT_CONVERSE_URL = "wss://agent.deepgram.com/v1/agent/converse"


@dataclass(frozen=True)
class DeepgramJsonEvent:
    """Normalized Deepgram event payload."""

    type: str
    role: str | None = None
    text: str | None = None
    raw: dict[str, Any] | None = None


def build_deepgram_settings(*, goal: str | None = None) -> dict[str, Any]:
    """Build a Deepgram Settings payload with Twilio-compatible audio config.

    When `goal` is present, we also add an initial agent greeting so realtime
    calls don't begin in silence waiting for user speech.
    """
    settings: dict[str, Any] = {
        "type": "Settings",
        "audio": {
            "input": {
                "encoding": "mulaw",
                "sample_rate": 8000,
            },
            "output": {
                "encoding": "mulaw",
                "sample_rate": 8000,
            },
        },
    }
    greeting = _goal_to_initial_greeting(goal)
    instructions = _build_agent_prompt(goal)
    agent: dict[str, Any] = {}
    if greeting:
        agent["greeting"] = greeting
    if instructions:
        think_provider = str(
            os.environ.get("ARCHON_VOICE_DEEPGRAM_THINK_PROVIDER", "open_ai") or "open_ai"
        ).strip() or "open_ai"
        think_model_raw = str(
            os.environ.get("ARCHON_VOICE_DEEPGRAM_THINK_MODEL", "gpt-4o-mini") or "gpt-4o-mini"
        ).strip() or "gpt-4o-mini"
        think_models = [part.strip() for part in think_model_raw.split(",") if part.strip()]
        if not think_models:
            think_models = ["gpt-4o-mini"]
        listen_model = str(
            os.environ.get("ARCHON_VOICE_DEEPGRAM_LISTEN_MODEL", "nova-3") or "nova-3"
        ).strip() or "nova-3"
        speak_model = str(
            os.environ.get("ARCHON_VOICE_DEEPGRAM_SPEAK_MODEL", "aura-2-asteria-en") or "aura-2-asteria-en"
        ).strip() or "aura-2-asteria-en"

        agent["listen"] = {
            "provider": {
                "type": "deepgram",
                "model": listen_model,
            },
        }
        if len(think_models) == 1:
            agent["think"] = {
                "provider": {
                    "type": think_provider,
                    "model": think_models[0],
                },
                "prompt": instructions,
            }
        else:
            agent["think"] = [
                {
                    "provider": {
                        "type": think_provider,
                        "model": model_name,
                    },
                    "prompt": instructions,
                }
                for model_name in think_models
            ]
        agent["speak"] = {
            "provider": {
                "type": "deepgram",
                "model": speak_model,
            },
        }
    if agent:
        settings["agent"] = agent
    return settings


def _build_agent_prompt(goal: str | None) -> str:
    raw = str(goal or "").strip()
    if not raw:
        return ""
    return (
        "You are Archon, a concise and pragmatic phone-call assistant.\n"
        f"Mission goal: {raw}\n"
        "Behavior:\n"
        "- Start immediately by delivering the mission goal; avoid generic openers.\n"
        "- Stay focused on the mission goal.\n"
        "- Keep responses short and natural for live phone conversation.\n"
        "- Follow the mission steps in order when sequencing is present (for example: first, then, finally).\n"
        "- Do not read meta-instructions verbatim; perform them naturally as conversation.\n"
        "- Do not invent unrelated tasks.\n"
        "- When the goal is complete, say a clear goodbye and end the conversation."
    )


def _goal_to_initial_greeting(goal: str | None) -> str:
    raw = str(goal or "").strip()
    if not raw:
        return ""

    match = re.match(
        r"^(?:please\s+)?(?:call\s+\w+\s+and\s+)?say\s+exactly\s+(.+)$",
        raw,
        flags=re.IGNORECASE,
    )
    if match:
        spoken = _dequote(match.group(1))
        return spoken or raw

    match = re.match(
        r"^(?:please\s+)?tell\s+(?:the\s+user|me)\s+a\s+joke\s*[:\-]\s*(.+)$",
        raw,
        flags=re.IGNORECASE,
    )
    if match:
        spoken = _dequote(match.group(1))
        return spoken or raw

    match = re.match(
        r"^(?:please\s+)?ask\s+(?:the\s+user|me|them)\s+(.+)$",
        raw,
        flags=re.IGNORECASE,
    )
    if match:
        body = _second_personize(match.group(1).strip())
        if body:
            body = re.sub(r"^\s*how\s+your\b", "How is your", body, flags=re.IGNORECASE)
            body = re.sub(r"^\s*how\s+you\b", "How are you", body, flags=re.IGNORECASE)
            body = re.sub(r"^(How is your .+?)\s+is going\b", r"\1 going", body, flags=re.IGNORECASE)
            body = body[:1].upper() + body[1:]
            if body.endswith("."):
                body = body[:-1] + "?"
            elif not body.endswith(("?", "!")):
                body = body + "?"
            return f"Hello, this is Archon. {body}"

    match = re.match(
        r"^(?:please\s+)?say\s+(.+?)(?:\s+to\s+(?:the\s+)?(?:user|me|them))?[.!?]?$",
        raw,
        flags=re.IGNORECASE,
    )
    if match:
        spoken = _second_personize(_dequote(match.group(1)))
        return _as_spoken_sentence(spoken)

    match = re.match(
        r"^(?:please\s+)?tell\s+(?:the\s+user|me|them)\s+(.+)$",
        raw,
        flags=re.IGNORECASE,
    )
    if match:
        spoken = _second_personize(_dequote(match.group(1)))
        return _as_spoken_sentence(spoken)

    # Multi-step instruction goals should start with a neutral greeting,
    # not by reading the instruction text verbatim.
    if _is_instructional_multi_step_goal(raw):
        return "Hello, this is Archon."

    return raw


def _dequote(text: str) -> str:
    value = str(text or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1].strip()
    return value


def _second_personize(text: str) -> str:
    out = str(text or "")
    replacements = [
        (r"\bthe user\b", "you"),
        (r"\btheir\b", "your"),
        (r"\btheirs\b", "yours"),
        (r"\bthem\b", "you"),
        (r"\bthey are\b", "you are"),
        (r"\bthey're\b", "you're"),
        (r"\bthey\b", "you"),
    ]
    for pattern, repl in replacements:
        out = re.sub(pattern, repl, out, flags=re.IGNORECASE)
    return out


def _as_spoken_sentence(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return value
    value = value[:1].upper() + value[1:]
    if value.endswith((".", "?", "!")):
        return value
    return f"{value}."


def _is_instructional_multi_step_goal(text: str) -> bool:
    value = str(text or "").strip().lower()
    if not value:
        return False
    sequence_markers = (" first", " then", " finally", " after that", " next")
    if not any(marker in f" {value}" for marker in sequence_markers):
        return False
    return any(verb in value for verb in ("greet", "ask", "tell", "say"))


def parse_deepgram_json_event(message: dict[str, Any]) -> DeepgramJsonEvent:
    """Normalize a Deepgram JSON event object into a small typed structure."""
    if not isinstance(message, dict):
        raise TypeError("message must be a dict")

    event_type = _require_str(message.get("type"), "type")
    role = _optional_str(message.get("role"), "role")

    text: str | None = None
    if event_type == "ConversationText":
        # Deepgram text events commonly surface text content under `content`.
        text = _optional_str(message.get("content"), "content")
        if text is None:
            text = _optional_str(message.get("text"), "text")

    return DeepgramJsonEvent(
        type=event_type,
        role=role,
        text=text,
        raw=dict(message),
    )


def build_keepalive_message() -> dict[str, str]:
    """Build a Deepgram keepalive payload."""
    return {"type": "KeepAlive"}


class DeepgramVoiceAgentClient:
    """Minimal async Deepgram Voice Agent websocket client.

    The websocket transport is injectable (`ws_factory`) so tests can provide a
    fake websocket object without a real network connection or API key.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        ws_url: str = DEEPGRAM_AGENT_CONVERSE_URL,
        ws_factory: Any | None = None,
        keepalive_interval_seconds: float = 5.0,
    ) -> None:
        self.api_key = str(api_key or "").strip()
        self.ws_url = str(ws_url or DEEPGRAM_AGENT_CONVERSE_URL)
        self._ws_factory = ws_factory or _default_ws_factory
        self.keepalive_interval_seconds = max(0.1, float(keepalive_interval_seconds or 5.0))

        self._ws: Any | None = None
        self._keepalive_task: asyncio.Task | None = None

    @property
    def is_connected(self) -> bool:
        return self._ws is not None

    async def connect(self) -> None:
        """Open the websocket transport."""
        if self._ws is not None:
            return
        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Token {self.api_key}"

        ws = self._ws_factory(self.ws_url, headers=headers)
        if inspect.isawaitable(ws):
            ws = await ws
        self._ws = ws

    async def connect_and_initialize(self, *, goal: str | None = None) -> None:
        """Connect and send the initial Deepgram `Settings` message."""
        await self.connect()
        await self.send_json(build_deepgram_settings(goal=goal))
        self.start_keepalive_loop()

    def start_keepalive_loop(self, *, interval_seconds: float | None = None) -> asyncio.Task | None:
        """Start the background periodic keepalive loop."""
        if self._keepalive_task is not None and not self._keepalive_task.done():
            return self._keepalive_task

        interval = (
            self.keepalive_interval_seconds
            if interval_seconds is None
            else max(0.1, float(interval_seconds))
        )
        self._keepalive_task = asyncio.create_task(self._keepalive_loop(interval))
        return self._keepalive_task

    async def stop_keepalive_loop(self) -> None:
        task = self._keepalive_task
        self._keepalive_task = None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def send_keepalive(self) -> None:
        await self.send_json(build_keepalive_message())

    async def send_json(self, message: dict[str, Any]) -> None:
        """Send a JSON object as a websocket text frame."""
        ws = self._require_ws()
        if hasattr(ws, "send_json"):
            await _maybe_await(ws.send_json(message))
            return
        payload = json.dumps(message, separators=(",", ":"))
        await _maybe_await(ws.send(payload))

    async def send_audio(self, chunk: bytes) -> None:
        """Send binary audio bytes to Deepgram."""
        if not isinstance(chunk, (bytes, bytearray)):
            raise TypeError("chunk must be bytes")
        ws = self._require_ws()
        data = bytes(chunk)
        if hasattr(ws, "send_bytes"):
            await _maybe_await(ws.send_bytes(data))
            return
        await _maybe_await(ws.send(data))

    async def receive(self) -> Any:
        """Receive a Deepgram frame, normalizing JSON text frames to Python objects."""
        ws = self._require_ws()
        if hasattr(ws, "recv"):
            frame = ws.recv()
        elif hasattr(ws, "receive"):
            frame = ws.receive()
        else:
            raise RuntimeError("websocket transport does not support recv/receive")
        if inspect.isawaitable(frame):
            frame = await frame

        if isinstance(frame, bytearray):
            return bytes(frame)
        if isinstance(frame, bytes):
            return frame
        if isinstance(frame, str):
            try:
                return json.loads(frame)
            except json.JSONDecodeError:
                return frame
        return frame

    async def close(self) -> None:
        """Stop keepalives and close the websocket."""
        await self.stop_keepalive_loop()
        ws = self._ws
        self._ws = None
        if ws is None:
            return
        if hasattr(ws, "close"):
            await _maybe_await(ws.close())

    async def _keepalive_loop(self, interval_seconds: float) -> None:
        try:
            while True:
                await asyncio.sleep(interval_seconds)
                await self.send_keepalive()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Keepalive errors should not crash the outer bridge loop.
            return

    def _require_ws(self) -> Any:
        if self._ws is None:
            raise RuntimeError("Deepgram websocket is not connected")
        return self._ws


async def _default_ws_factory(url: str, *, headers: dict[str, str] | None = None) -> Any:
    """Create a live websocket transport using the optional `websockets` package."""
    try:
        import websockets  # type: ignore
    except Exception as exc:  # pragma: no cover - import path depends on env
        raise RuntimeError("websockets package is required for live Deepgram connections") from exc

    connect_fn = getattr(websockets, "connect")
    connect_kwargs: dict[str, Any] = {}
    if headers:
        try:
            params = inspect.signature(connect_fn).parameters
        except (TypeError, ValueError):  # pragma: no cover - defensive
            params = {}

        if "additional_headers" in params:
            connect_kwargs["additional_headers"] = headers
        elif "extra_headers" in params:
            connect_kwargs["extra_headers"] = headers
        elif "headers" in params:
            connect_kwargs["headers"] = headers

    connection = connect_fn(url, **connect_kwargs)
    if inspect.isawaitable(connection):
        return await connection
    return connection


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _require_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or value == "":
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _optional_str(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string when provided")
    return value


__all__ = [
    "DEEPGRAM_AGENT_CONVERSE_URL",
    "DeepgramJsonEvent",
    "DeepgramVoiceAgentClient",
    "build_deepgram_settings",
    "build_keepalive_message",
    "parse_deepgram_json_event",
]
