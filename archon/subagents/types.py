"""Native subagent type definitions and tier mapping."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SubagentType:
    name: str
    tier: str
    allowed_tools: tuple[str, ...]

    def allows(self, tool_name: str) -> bool:
        return str(tool_name or "").strip().lower() in set(self.allowed_tools)


EXPLORE_SUBAGENT_TYPE = SubagentType(
    name="explore",
    tier="light",
    allowed_tools=("read_file", "grep", "glob", "list_dir", "shell"),
)

GENERAL_SUBAGENT_TYPE = SubagentType(
    name="general",
    tier="standard",
    allowed_tools=(
        "read_file",
        "grep",
        "glob",
        "list_dir",
        "shell",
        "write_file",
        "edit_file",
        "news_brief",
        "web_search",
        "web_read",
        "deep_research",
        "check_research_job",
        "list_research_jobs",
    ),
)

explore = EXPLORE_SUBAGENT_TYPE
general = GENERAL_SUBAGENT_TYPE

_SUBAGENT_TYPES = {
    explore.name: explore,
    general.name: general,
}


def get_subagent_type(name: str) -> SubagentType:
    key = str(name or "").strip().lower()
    if key not in _SUBAGENT_TYPES:
        raise KeyError(f"Unknown subagent type: {name}")
    return _SUBAGENT_TYPES[key]


def iter_subagent_types() -> tuple[SubagentType, ...]:
    return tuple(_SUBAGENT_TYPES.values())
