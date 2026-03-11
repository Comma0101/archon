"""Tests for human-handoff setup tools."""

from archon.tools import ToolRegistry


def test_tool_registry_ask_human_returns_structured_suspension():
    from archon.execution.contracts import SuspensionRequest

    registry = ToolRegistry(archon_source_dir=None)

    result = registry.execute(
        "ask_human",
        {
            "question": "Please provide OPENAI_API_KEY",
            "context": "Sign up at https://platform.openai.com/api-keys",
            "project": "browser-use",
        },
    )

    assert isinstance(result, SuspensionRequest)
    assert result.reason == "needs_human_input"
    assert result.question == "Please provide OPENAI_API_KEY"
    assert result.project == "browser-use"


def test_format_suspension_request_renders_question_and_context():
    from archon.execution.contracts import SuspensionRequest, format_suspension_request

    request = SuspensionRequest(
        reason="needs_human_input",
        question="Provide OPENAI_API_KEY",
        context="Sign up first.",
        project="browser-use",
    )

    text = format_suspension_request(request)

    assert "Human input needed" in text
    assert "Provide OPENAI_API_KEY" in text
    assert "Sign up first." in text
