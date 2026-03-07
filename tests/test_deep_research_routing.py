"""Tests for deep research topic validation."""

from archon.control.orchestrator import is_deep_research_request


def test_bare_deep_research_not_routed():
    """Bare 'can you do deep research' should NOT trigger a research job."""
    assert is_deep_research_request("can you do deep research") is False
    assert is_deep_research_request("do deep research") is False
    assert is_deep_research_request("deep research") is False


def test_deep_research_with_topic_is_routed():
    """Deep research with an actual topic should trigger."""
    assert is_deep_research_request("deep research on multi agent AI in 2026") is True
    assert is_deep_research_request("can you do deep research on voice AI applications") is True


def test_short_deep_research_not_routed():
    """Very short prompts with 'deep research' should not trigger — likely a question."""
    assert is_deep_research_request("deep research please") is False
    assert is_deep_research_request("do a deep research") is False
