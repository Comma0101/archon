"""Tests for llm.py helper conversions and OpenAI message flattening."""

from types import SimpleNamespace

from archon.llm import LLMClient


def _make_openai_client():
    captured = {}

    class _Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="ok", tool_calls=None)
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
            )

    client = object.__new__(LLMClient)
    client.provider = "openai"
    client.model = "test-model"
    client.temperature = 0.0
    client.client = SimpleNamespace(
        chat=SimpleNamespace(completions=_Completions())
    )
    return client, captured


class TestOpenAIConversion:
    def test_convert_user_message_with_multiple_tool_results_returns_list(self):
        client, _captured = _make_openai_client()
        msg = {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "tc_1", "content": "first"},
                {"type": "tool_result", "tool_use_id": "tc_2", "content": "second"},
            ],
        }

        converted = client._convert_message_to_openai(msg)
        assert isinstance(converted, list)
        assert len(converted) == 2
        assert converted[0]["tool_call_id"] == "tc_1"
        assert converted[1]["tool_call_id"] == "tc_2"

    def test_chat_openai_flattens_multiple_tool_results_into_messages(self):
        client, captured = _make_openai_client()
        messages = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tc_1",
                        "name": "shell",
                        "input": {"command": "echo one"},
                    },
                    {
                        "type": "tool_use",
                        "id": "tc_2",
                        "name": "shell",
                        "input": {"command": "echo two"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tc_1", "content": "one"},
                    {"type": "tool_result", "tool_use_id": "tc_2", "content": "two"},
                ],
            },
        ]

        resp = client._chat_openai("sys", messages, tools=None)

        assert resp.text == "ok"
        oai_messages = captured["messages"]
        assert oai_messages[0]["role"] == "system"
        tool_msgs = [m for m in oai_messages if isinstance(m, dict) and m.get("role") == "tool"]
        assert len(tool_msgs) == 2
        assert {m["tool_call_id"] for m in tool_msgs} == {"tc_1", "tc_2"}
