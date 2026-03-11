"""SKILL.md loading and compilation tests."""

from pathlib import Path
from archon.skills.loader import load_markdown_skills, MarkdownSkill


def test_load_skill_from_folder(tmp_path):
    skill_dir = tmp_path / "deploy-korami"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("""---
name: deploy-korami
description: Deploy korami-site to Vercel
triggers:
  - deploy korami
  - push korami to production
requires:
  bins: [bun, vercel]
  env: [VERCEL_TOKEN]
tools: [shell, read_file]
timeout: 300
---

## Steps

1. cd ~/Documents/korami-site
2. Run bun install
3. Run bun run build
4. Run vercel --prod
""")

    skills = load_markdown_skills(tmp_path)
    assert len(skills) == 1
    skill = skills[0]
    assert skill.name == "deploy-korami"
    assert "deploy korami" in skill.triggers
    assert "shell" in skill.allowed_tools
    assert skill.timeout == 300


def test_load_skill_without_frontmatter(tmp_path):
    skill_dir = tmp_path / "simple"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Simple Skill\nJust some instructions.\n")
    skills = load_markdown_skills(tmp_path)
    assert len(skills) == 1
    assert skills[0].name == "simple"  # falls back to folder name


def test_load_empty_dir(tmp_path):
    skills = load_markdown_skills(tmp_path)
    assert skills == []


def test_skill_to_profile_kwargs():
    skill = MarkdownSkill(
        name="test", description="A test", triggers=["do test"],
        allowed_tools=["shell"], requires_bins=[], requires_env=[],
        timeout=60, content="## Steps\n1. Do thing\n",
    )
    profile = skill.to_profile_kwargs()
    assert profile["skill_name"] == "test"
    assert "shell" in profile["allowed_tools"]
    assert "## Steps" in profile["prompt_guidance"]


def test_load_nonexistent_dir():
    skills = load_markdown_skills(Path("/tmp/nonexistent_skill_dir_xyz"))
    assert skills == []


def test_load_skips_non_directories(tmp_path):
    (tmp_path / "stray-file.txt").write_text("not a skill")
    skills = load_markdown_skills(tmp_path)
    assert skills == []


def test_load_skips_folder_without_skill_md(tmp_path):
    (tmp_path / "no-skill").mkdir()
    (tmp_path / "no-skill" / "README.md").write_text("not a skill")
    skills = load_markdown_skills(tmp_path)
    assert skills == []


def test_skill_requires_bins_and_env(tmp_path):
    skill_dir = tmp_path / "needs-stuff"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("""---
name: needs-stuff
requires:
  bins: [docker, kubectl]
  env: [KUBECONFIG]
---
Deploy to k8s.
""")
    skills = load_markdown_skills(tmp_path)
    assert len(skills) == 1
    assert skills[0].requires_bins == ["docker", "kubectl"]
    assert skills[0].requires_env == ["KUBECONFIG"]


def test_description_prepended_to_guidance():
    skill = MarkdownSkill(
        name="test", description="Short desc",
        content="## Steps\n1. Do thing\n",
    )
    profile = skill.to_profile_kwargs()
    assert profile["prompt_guidance"].startswith("Short desc")
    assert "## Steps" in profile["prompt_guidance"]


def test_description_not_duplicated_if_in_content():
    skill = MarkdownSkill(
        name="test", description="Short desc",
        content="Short desc\n\n## Steps\n1. Do thing\n",
    )
    profile = skill.to_profile_kwargs()
    # description already in content, should not be doubled
    assert profile["prompt_guidance"].count("Short desc") == 1


def test_markdown_skill_trigger_match(tmp_path, monkeypatch):
    """A markdown skill can be matched from natural-language triggers."""
    from archon.control.skills import find_markdown_skill_match, _loaded_markdown_skills
    skill_dir = tmp_path / "my-deploy"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-deploy\ntriggers:\n  - deploy my app\n---\nDeploy stuff.\n"
    )

    monkeypatch.setattr("archon.control.skills._MARKDOWN_SKILLS_DIR", tmp_path)
    _loaded_markdown_skills.cache_clear()

    skill = find_markdown_skill_match("please deploy my app now")
    assert skill is not None
    assert skill.name == "my-deploy"

    _loaded_markdown_skills.cache_clear()


def test_markdown_skill_no_match_on_unrelated(tmp_path, monkeypatch):
    """Unrelated text does not trigger a markdown skill."""
    from archon.control.skills import find_markdown_skill_match, _loaded_markdown_skills
    skill_dir = tmp_path / "my-deploy"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-deploy\ntriggers:\n  - deploy my app\n---\nDeploy stuff.\n"
    )

    monkeypatch.setattr("archon.control.skills._MARKDOWN_SKILLS_DIR", tmp_path)
    _loaded_markdown_skills.cache_clear()

    skill = find_markdown_skill_match("what's the weather like?")
    assert skill is None

    _loaded_markdown_skills.cache_clear()


def test_markdown_skill_empty_text():
    from archon.control.skills import find_markdown_skill_match
    assert find_markdown_skill_match("") is None
    assert find_markdown_skill_match(None) is None
