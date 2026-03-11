"""Load SKILL.md folders and expose runtime metadata for skill activation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class MarkdownSkill:
    name: str
    description: str = ""
    triggers: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=lambda: ["*"])
    requires_bins: list[str] = field(default_factory=list)
    requires_env: list[str] = field(default_factory=list)
    timeout: int = 300
    content: str = ""  # the markdown body after frontmatter

    def to_profile_kwargs(self) -> dict[str, Any]:
        guidance = self.content.strip()
        if self.description and self.description not in guidance:
            guidance = f"{self.description}\n\n{guidance}"
        return {
            "skill_name": self.name,
            "allowed_tools": list(self.allowed_tools) if self.allowed_tools else ["*"],
            "prompt_guidance": guidance,
            "triggers": list(self.triggers),
            "timeout": self.timeout,
        }


def load_markdown_skills(skills_dir: Path) -> list[MarkdownSkill]:
    """Scan a directory for SKILL.md folders and load them."""
    if not skills_dir.exists():
        return []

    skills: list[MarkdownSkill] = []
    for child in sorted(skills_dir.iterdir()):
        if not child.is_dir():
            continue
        skill_file = child / "SKILL.md"
        if not skill_file.exists():
            continue
        try:
            skill = _parse_skill_file(skill_file, folder_name=child.name)
            skills.append(skill)
        except Exception:
            continue
    return skills


def _parse_skill_file(path: Path, folder_name: str) -> MarkdownSkill:
    text = path.read_text(errors="replace")
    frontmatter, body = _split_frontmatter(text)

    name = str(frontmatter.get("name", folder_name)).strip() or folder_name
    description = str(frontmatter.get("description", "")).strip()
    triggers = _as_list(frontmatter.get("triggers", []))
    tools = _as_list(frontmatter.get("tools", ["*"]))
    timeout = int(frontmatter.get("timeout", 300))

    requires = frontmatter.get("requires", {})
    if isinstance(requires, dict):
        bins = _as_list(requires.get("bins", []))
        env = _as_list(requires.get("env", []))
    else:
        bins, env = [], []

    return MarkdownSkill(
        name=name, description=description, triggers=triggers,
        allowed_tools=tools, requires_bins=bins, requires_env=env,
        timeout=timeout, content=body,
    )


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Split YAML frontmatter from markdown body."""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)", text, re.DOTALL)
    if not match:
        return {}, text

    yaml_text = match.group(1)
    body = match.group(2)

    data = _parse_simple_yaml(yaml_text)
    return data, body


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse simple YAML (key: value, key: [list], nested one level)."""
    result: dict[str, Any] = {}
    current_key = ""
    current_list: list[str] | None = None
    current_dict: dict[str, Any] | None = None

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())

        # List item under a key
        if stripped.startswith("- ") and current_key and indent > 0:
            value = stripped[2:].strip()
            if current_list is not None:
                current_list.append(value)
            continue

        # Nested key: value under a parent key
        if ":" in stripped and indent > 0 and current_dict is not None:
            k, _, v = stripped.partition(":")
            v = v.strip()
            if v.startswith("[") and v.endswith("]"):
                current_dict[k.strip()] = [x.strip() for x in v[1:-1].split(",") if x.strip()]
            else:
                current_dict[k.strip()] = v
            continue

        # Top-level key: value
        if ":" in stripped and indent == 0:
            if current_key and current_list is not None:
                result[current_key] = current_list
            elif current_key and current_dict is not None:
                result[current_key] = current_dict

            k, _, v = stripped.partition(":")
            current_key = k.strip()
            v = v.strip()
            current_list = None
            current_dict = None

            if v.startswith("[") and v.endswith("]"):
                result[current_key] = [x.strip() for x in v[1:-1].split(",") if x.strip()]
                current_key = ""
            elif v:
                result[current_key] = v
                current_key = ""
            else:
                # Could be start of list or nested dict — peek ahead
                current_list = []
                current_dict = {}

    # Flush last
    if current_key:
        if current_list:
            result[current_key] = current_list
        elif current_dict:
            result[current_key] = current_dict

    return result


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []
