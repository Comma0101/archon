"""Capability assessment tests."""

import os
from archon.setup.scanner import ProjectProfile
from archon.setup.assessor import assess_capabilities, AssessmentResult


def test_env_var_already_set(monkeypatch):
    monkeypatch.setenv("EXISTING_KEY", "value")
    profile = ProjectProfile(
        project_path="/tmp/test", project_name="test",
        env_vars=["EXISTING_KEY"],
    )
    result = assess_capabilities(profile)
    assert len(result.already_done) == 1
    assert "EXISTING_KEY" in result.already_done[0]


def test_sensitive_env_var_needs_human():
    profile = ProjectProfile(
        project_path="/tmp/test", project_name="test",
        env_vars=["OPENAI_API_KEY", "DATABASE_URL"],
    )
    result = assess_capabilities(profile)
    human_vars = [s.env_var for s in result.needs_human if s.env_var]
    assert "OPENAI_API_KEY" in human_vars


def test_nonsensitive_env_var_archon_can_handle():
    profile = ProjectProfile(
        project_path="/tmp/test", project_name="test",
        env_vars=["DATABASE_URL", "PORT"],
    )
    result = assess_capabilities(profile)
    assert any("DATABASE_URL" in desc or "PORT" in desc for desc in result.archon_can)


def test_assessment_result_to_steps():
    profile = ProjectProfile(
        project_path="/tmp/test", project_name="test",
        env_vars=["SECRET_KEY"],
    )
    result = assess_capabilities(profile)
    steps = result.to_setup_steps()
    assert len(steps) > 0
    assert any(s.kind == "human" for s in steps)


def test_install_scripts_detected():
    profile = ProjectProfile(
        project_path="/tmp/test", project_name="test",
        scripts={"install": "npm install", "dev": "next dev"},
    )
    result = assess_capabilities(profile)
    assert any("install" in desc.lower() for desc in result.archon_can)


def test_python_deps_detected():
    profile = ProjectProfile(
        project_path="/tmp/test", project_name="test",
        discovery_sources=["requirements.txt"],
    )
    result = assess_capabilities(profile)
    assert any("pip install" in desc for desc in result.archon_can)


def test_rust_build_detected():
    profile = ProjectProfile(
        project_path="/tmp/test", project_name="test",
        discovery_sources=["Cargo.toml"],
    )
    result = assess_capabilities(profile)
    assert any("cargo build" in desc for desc in result.archon_can)


def test_signup_hints():
    profile = ProjectProfile(
        project_path="/tmp/test", project_name="test",
        env_vars=["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"],
    )
    result = assess_capabilities(profile)
    hints = {r.env_var: r.hint for r in result.needs_human}
    assert "platform.openai.com" in hints.get("OPENAI_API_KEY", "")
    assert "console.anthropic.com" in hints.get("ANTHROPIC_API_KEY", "")
    assert "aistudio.google.com" in hints.get("GOOGLE_API_KEY", "")


def test_step_ids_sequential():
    profile = ProjectProfile(
        project_path="/tmp/test", project_name="test",
        env_vars=["SECRET_KEY", "PORT"],
        discovery_sources=["requirements.txt"],
    )
    result = assess_capabilities(profile)
    steps = result.to_setup_steps()
    ids = [s.step_id for s in steps]
    assert ids == list(range(1, len(ids) + 1))
