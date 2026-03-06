"""System prompt assembly from templates and runtime data."""

from pathlib import Path

from archon.system import get_profile, format_profile
from archon.introspect import format_self_awareness
from archon.memory import summary as memory_summary


PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def load_template(name: str) -> str:
    """Load a prompt template from the prompts/ directory."""
    path = PROMPTS_DIR / name
    if not path.exists():
        return ""
    return path.read_text()


def build_system_prompt(tool_count: int = 7) -> str:
    """Assemble the full system prompt from template + runtime data."""
    template = load_template("system.md")
    guidelines = load_template("guidelines.md")

    profile = get_profile()
    system_profile = format_profile(profile)
    self_awareness = format_self_awareness()
    mem_summary = memory_summary()

    if mem_summary:
        mem_section = f"Your persistent memory:\n```\n{mem_summary}\n```"
    else:
        mem_section = "No persistent memory yet. Use memory_write to save important context."

    prompt = template.format(
        system_profile=system_profile,
        tool_count=tool_count,
        self_awareness=self_awareness,
        memory_summary=mem_section,
        guidelines=guidelines,
    )

    return prompt.strip()
