"""Tests for native research clients and stores."""

import pytest

from archon.research.google_deep_research import GoogleDeepResearchClient


class _FakeInteractionsClient:
    def __init__(self):
        self.create_calls = []
        self.get_calls = []

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return {
            "id": "int-123",
            "status": "running",
            "response": None,
        }

    def get(self, interaction_id: str):
        self.get_calls.append(interaction_id)
        return {
            "id": interaction_id,
            "status": "completed",
            "response": {
                "output_text": "done",
            },
        }


def test_google_deep_research_client_starts_background_interaction():
    fake = _FakeInteractionsClient()
    client = GoogleDeepResearchClient(fake, agent="deep-research-pro-preview-12-2025")

    result = client.start_research("Research LA restaurant market")

    assert result.interaction_id == "int-123"
    assert result.status == "running"
    assert fake.create_calls == [
        {
            "agent": "deep-research-pro-preview-12-2025",
            "input": "Research LA restaurant market",
            "background": True,
            "store": True,
            "tools": None,
        }
    ]


def test_google_deep_research_client_loads_interaction_status():
    fake = _FakeInteractionsClient()
    client = GoogleDeepResearchClient(fake, agent="deep-research-pro-preview-12-2025")

    result = client.get_research("int-123")

    assert result.interaction_id == "int-123"
    assert result.status == "completed"
    assert result.output_text == "done"
    assert fake.get_calls == ["int-123"]


def test_google_deep_research_client_rejects_custom_tools():
    fake = _FakeInteractionsClient()
    client = GoogleDeepResearchClient(fake, agent="deep-research-pro-preview-12-2025")

    with pytest.raises(ValueError, match="only supports built-in web research and optional file_search"):
        client.start_research(
            "Research LA restaurant market",
            tools=[{"type": "mcp", "server": "exa"}],
        )
