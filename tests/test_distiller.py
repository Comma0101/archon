"""Session distillation tests."""

from archon.distiller import build_distillation_prompt, parse_distillation_output


def test_build_distillation_prompt():
    messages = [
        {"role": "user", "content": "set up browser-use"},
        {"role": "assistant", "content": "I installed the dependencies and configured chromium."},
    ]
    prompt = build_distillation_prompt(messages)
    assert "extract" in prompt.lower() or "analyze" in prompt.lower()


def test_parse_distillation_output_facts():
    llm_output = """
FACT|high|project:browser-use|browser-use requires chromium and OPENAI_API_KEY|projects/browser-use.md
PROCEDURE|high|project:browser-use|To run: source .venv/bin/activate && python script.py|projects/browser-use.md
CORRECTION|high|global|bun is preferred over npm for korami-site|projects/korami-site.md
GAP|medium|global|User wanted to send email via browser-use but it failed|capability_gaps.md
"""
    items = parse_distillation_output(llm_output)
    assert len(items) == 4
    assert items[0]["kind"] == "fact"
    assert items[0]["confidence"] == "high"
    assert items[1]["kind"] == "procedure"
    assert items[2]["kind"] == "correction"
    assert items[3]["kind"] == "gap"


def test_parse_distillation_output_none():
    items = parse_distillation_output("NONE")
    assert items == []


def test_parse_distillation_output_empty():
    items = parse_distillation_output("")
    assert items == []


def test_parse_distillation_output_skips_invalid():
    llm_output = "INVALID|high|global|something\nFACT|high|global|valid thing|file.md"
    items = parse_distillation_output(llm_output)
    assert len(items) == 1
    assert items[0]["kind"] == "fact"


def test_parse_distillation_output_normalizes_confidence():
    llm_output = "FACT|EXTREME|global|something|file.md"
    items = parse_distillation_output(llm_output)
    assert len(items) == 1
    assert items[0]["confidence"] == "medium"  # normalized


def test_build_distillation_prompt_truncates():
    messages = [{"role": "user", "content": "x" * 20000}]
    prompt = build_distillation_prompt(messages, max_chars=100)
    # Should not include the oversized message
    assert "x" * 20000 not in prompt
