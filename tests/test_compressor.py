"""LLM-powered context compression tests."""

from archon.compressor import build_compression_prompt, parse_compression_result


def test_build_compression_prompt():
    messages = [
        {"role": "user", "content": "deploy korami"},
        {"role": "assistant", "content": "I'll deploy korami-site using bun and vercel."},
    ]
    prompt = build_compression_prompt(messages)
    assert "deploy" in prompt.lower() or "summarize" in prompt.lower()
    assert "korami" in prompt


def test_parse_compression_result():
    llm_output = "User asked to deploy korami-site. Archon ran bun build successfully."
    result = parse_compression_result(llm_output, layer="session", summary_id="test-1")
    assert result["layer"] == "session"
    assert "korami" in result["content"]
    assert result["summary_id"] == "test-1"


def test_parse_compression_result_empty():
    result = parse_compression_result("", layer="session")
    assert "No summary" in result["content"]


def test_build_compression_prompt_truncates():
    messages = [{"role": "user", "content": "x" * 10000}]
    prompt = build_compression_prompt(messages, max_chars=100)
    assert len(prompt) < 10000


def test_flatten_tool_use_content():
    messages = [
        {"role": "assistant", "content": [
            {"type": "text", "text": "Running command"},
            {"type": "tool_use", "name": "shell"},
        ]},
    ]
    prompt = build_compression_prompt(messages)
    assert "Running command" in prompt
    assert "[tool: shell]" in prompt


def test_prefetch_defaults_increased():
    """Memory prefetch should now return more results with larger excerpts."""
    from archon.memory import prefetch_for_query  # noqa: memory.py, not memory/
    import inspect
    sig = inspect.signature(prefetch_for_query)
    limit_default = sig.parameters["limit"].default
    max_chars_default = sig.parameters["max_chars_per_file"].default
    assert limit_default >= 4, f"prefetch limit should be >= 4, got {limit_default}"
    assert max_chars_default >= 1500, f"max_chars should be >= 1500, got {max_chars_default}"
