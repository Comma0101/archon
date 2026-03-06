"""Tests for Deepgram Voice Agent protocol message helpers."""

import asyncio
import json
from dataclasses import is_dataclass
from importlib import import_module

import pytest


def _deepgram_agent_module():
    return import_module("services.archon_voice.deepgram_agent")


def test_build_deepgram_settings_uses_twilio_mulaw_8khz_audio():
    deepgram_agent = _deepgram_agent_module()

    message = deepgram_agent.build_deepgram_settings()

    assert message["type"] == "Settings"
    assert message["audio"]["input"]["encoding"] == "mulaw"
    assert message["audio"]["input"]["sample_rate"] == 8000
    assert message["audio"]["output"]["encoding"] == "mulaw"
    assert message["audio"]["output"]["sample_rate"] == 8000


def test_build_deepgram_settings_goal_joke_adds_agent_greeting():
    deepgram_agent = _deepgram_agent_module()

    message = deepgram_agent.build_deepgram_settings(
        goal="Tell the user a joke: Why do programmers prefer dark mode? Because light attracts bugs!"
    )

    assert message["agent"]["greeting"] == "Why do programmers prefer dark mode? Because light attracts bugs!"
    assert "You are Archon" in message["agent"]["think"]["prompt"]
    assert "Mission goal: Tell the user a joke" in message["agent"]["think"]["prompt"]
    assert message["agent"]["think"]["provider"]["type"] == "open_ai"
    assert message["agent"]["think"]["provider"]["model"] == "gpt-4o-mini"
    assert message["agent"]["listen"]["provider"]["type"] == "deepgram"
    assert message["agent"]["listen"]["provider"]["model"] == "nova-3"
    assert message["agent"]["speak"]["provider"]["type"] == "deepgram"
    assert message["agent"]["speak"]["provider"]["model"] == "aura-2-asteria-en"


def test_build_deepgram_settings_goal_say_exactly_dequotes_for_greeting():
    deepgram_agent = _deepgram_agent_module()

    message = deepgram_agent.build_deepgram_settings(goal="Say exactly 'hi how you doing'")

    assert message["agent"]["greeting"] == "hi how you doing"
    assert "Mission goal: Say exactly 'hi how you doing'" in message["agent"]["think"]["prompt"]


def test_build_deepgram_settings_goal_say_to_user_normalizes_for_spoken_greeting():
    deepgram_agent = _deepgram_agent_module()

    message = deepgram_agent.build_deepgram_settings(goal="Say hi to the user.")

    assert message["agent"]["greeting"] == "Hi."
    assert "user" not in message["agent"]["greeting"].lower()


def test_build_deepgram_settings_goal_prompt_requires_mission_first_response():
    deepgram_agent = _deepgram_agent_module()

    message = deepgram_agent.build_deepgram_settings(goal="Say hi to the user.")

    prompt = message["agent"]["think"]["prompt"]
    assert "Mission goal: Say hi to the user." in prompt
    assert "Start immediately by delivering the mission goal" in prompt


def test_build_deepgram_settings_multi_step_goal_uses_safe_polite_opening():
    deepgram_agent = _deepgram_agent_module()

    message = deepgram_agent.build_deepgram_settings(
        goal="Politely greet the user first, then ask them how their trading session went, and finally tell them a joke."
    )

    assert message["agent"]["greeting"] == "Hello, this is Archon."
    assert "trading session" not in message["agent"]["greeting"].lower()


def test_build_deepgram_settings_multi_step_goal_prompt_enforces_order():
    deepgram_agent = _deepgram_agent_module()

    message = deepgram_agent.build_deepgram_settings(
        goal="Politely greet the user first, then ask them how their trading session went, and finally tell them a joke."
    )
    prompt = message["agent"]["think"]["prompt"]

    assert "Follow the mission steps in order when sequencing is present" in prompt
    assert "Do not read meta-instructions verbatim" in prompt


def test_build_deepgram_settings_without_goal_omits_agent_prompt():
    deepgram_agent = _deepgram_agent_module()

    message = deepgram_agent.build_deepgram_settings(goal="")

    assert "agent" not in message


def test_build_deepgram_settings_supports_think_model_fallback_list_from_env(monkeypatch):
    deepgram_agent = _deepgram_agent_module()
    monkeypatch.setenv("ARCHON_VOICE_DEEPGRAM_THINK_PROVIDER", "open_ai")
    monkeypatch.setenv("ARCHON_VOICE_DEEPGRAM_THINK_MODEL", "gpt-5.2-instant,gpt-4o-mini")

    message = deepgram_agent.build_deepgram_settings(goal="Ask for store hours")

    think = message["agent"]["think"]
    assert isinstance(think, list)
    assert len(think) == 2
    assert think[0]["provider"]["type"] == "open_ai"
    assert think[0]["provider"]["model"] == "gpt-5.2-instant"
    assert think[1]["provider"]["model"] == "gpt-4o-mini"
    assert "Mission goal: Ask for store hours" in think[0]["prompt"]


def test_parse_deepgram_json_event_conversation_text():
    deepgram_agent = _deepgram_agent_module()

    event = deepgram_agent.parse_deepgram_json_event(
        {
            "type": "ConversationText",
            "role": "assistant",
            "content": "Hello from Deepgram.",
        }
    )

    assert is_dataclass(event)
    assert event.type == "ConversationText"
    assert event.role == "assistant"
    assert event.text == "Hello from Deepgram."


def test_build_keepalive_message():
    deepgram_agent = _deepgram_agent_module()

    assert deepgram_agent.build_keepalive_message() == {"type": "KeepAlive"}


class _FakeDeepgramWs:
    def __init__(self, recv_frames=None) -> None:
        self.recv_frames = list(recv_frames or [])
        self.sent_frames: list[object] = []
        self.closed = False

    async def send(self, payload: object) -> None:
        self.sent_frames.append(payload)

    async def recv(self):
        if self.recv_frames:
            return self.recv_frames.pop(0)
        await asyncio.sleep(0)
        return None

    async def close(self) -> None:
        self.closed = True


@pytest.mark.anyio
async def test_deepgram_client_connects_and_sends_settings_first_and_keepalive():
    deepgram_agent = _deepgram_agent_module()
    fake_ws = _FakeDeepgramWs()
    captured: dict[str, object] = {}

    async def _fake_factory(url: str, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return fake_ws

    client = deepgram_agent.DeepgramVoiceAgentClient(
        api_key="dg-test-key",
        ws_factory=_fake_factory,
        keepalive_interval_seconds=60.0,
    )

    await client.connect_and_initialize(goal="Ask about trading")
    await client.send_keepalive()
    await client.close()

    assert captured["url"] == "wss://agent.deepgram.com/v1/agent/converse"
    assert json.loads(str(fake_ws.sent_frames[0]))["type"] == "Settings"
    assert json.loads(str(fake_ws.sent_frames[1])) == {"type": "KeepAlive"}
    assert fake_ws.closed is True


@pytest.mark.anyio
async def test_deepgram_client_exposes_json_and_binary_send_receive_apis():
    deepgram_agent = _deepgram_agent_module()
    fake_ws = _FakeDeepgramWs(
        recv_frames=[
            '{"type":"ConversationText","role":"assistant","content":"hi"}',
            b"\x01\x02\x03",
        ]
    )
    client = deepgram_agent.DeepgramVoiceAgentClient(ws_factory=lambda *_args, **_kwargs: fake_ws)

    await client.connect()
    await client.send_json({"type": "Ping"})
    await client.send_audio(b"abc")

    first = await client.receive()
    second = await client.receive()

    assert json.loads(str(fake_ws.sent_frames[0])) == {"type": "Ping"}
    assert fake_ws.sent_frames[1] == b"abc"
    assert isinstance(first, dict)
    assert first["type"] == "ConversationText"
    assert second == b"\x01\x02\x03"
