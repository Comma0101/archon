"""Auto-generate SKILL.md from successful sessions."""

from __future__ import annotations

from pathlib import Path


SKILL_GENERATION_PROMPT = """\
Generate a SKILL.md file for the project "{project_name}".

Based on the procedures that worked during this session:
{procedures}

The SKILL.md should follow this format:
---
name: {project_name}
description: <one-line description>
triggers:
  - <natural trigger phrase 1>
  - <natural trigger phrase 2>
requires:
  bins: [<required binaries>]
  env: [<required env vars>]
tools: [shell, read_file, write_file]
---

## Steps
<numbered steps that worked>

## Known Issues
<any issues encountered and their solutions>

## Error Recovery
<what to do when common errors occur>

Output ONLY the SKILL.md content, nothing else.
"""


def build_skill_generation_prompt(
    project_name: str,
    procedures: list[str],
    known_issues: list[str] | None = None,
) -> str:
    proc_text = "\n".join(procedures) if procedures else "(none recorded)"
    prompt = SKILL_GENERATION_PROMPT.format(
        project_name=project_name,
        procedures=proc_text,
    )
    if known_issues:
        prompt += f"\n\nKnown issues encountered:\n" + "\n".join(known_issues)
    return prompt


def write_skill_folder(
    skills_dir: Path,
    skill_name: str,
    skill_content: str,
) -> Path:
    """Write a SKILL.md file to a skill folder."""
    folder = skills_dir / skill_name
    folder.mkdir(parents=True, exist_ok=True)
    skill_file = folder / "SKILL.md"
    skill_file.write_text(skill_content)
    return skill_file
