"""SKILL.md generation tests."""

from pathlib import Path
from archon.skills.generator import build_skill_generation_prompt, write_skill_folder


def test_build_skill_generation_prompt():
    project_name = "browser-use"
    procedures = [
        "1. source .venv/bin/activate",
        "2. python script.py",
    ]
    prompt = build_skill_generation_prompt(project_name, procedures)
    assert "browser-use" in prompt
    assert "SKILL.md" in prompt or "skill" in prompt.lower()


def test_write_skill_folder(tmp_path):
    skill_content = """---
name: test-skill
description: A test
triggers:
  - do test
---
## Steps
1. Run test
"""
    path = write_skill_folder(tmp_path, "test-skill", skill_content)
    assert (tmp_path / "test-skill" / "SKILL.md").exists()
    assert "test-skill" in str(path)


def test_build_prompt_with_known_issues():
    prompt = build_skill_generation_prompt(
        "my-project",
        ["1. run build"],
        known_issues=["OOM on large inputs"],
    )
    assert "OOM" in prompt


def test_build_prompt_empty_procedures():
    prompt = build_skill_generation_prompt("empty", [])
    assert "none recorded" in prompt


def test_write_skill_folder_creates_parents(tmp_path):
    path = write_skill_folder(tmp_path / "deep" / "nested", "my-skill", "# Skill\n")
    assert path.exists()
    assert path.read_text() == "# Skill\n"
