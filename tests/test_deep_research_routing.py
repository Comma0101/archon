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


def test_preamble_before_deep_research_not_counted_as_topic():
    """Conversational preamble before 'deep research' should not count as topic."""
    assert is_deep_research_request("yo whats up, can you do a deep research") is False
    assert is_deep_research_request("hey there do deep research") is False
    assert is_deep_research_request("hello can you do deep research for me") is False


def test_preamble_with_topic_after_still_routes():
    """Preamble is fine as long as there's a real topic AFTER the trigger."""
    assert is_deep_research_request("hey can you do deep research on quantum computing applications") is True
