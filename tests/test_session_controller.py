"""Tests for setup-job resume matching helpers."""

from archon.control import session_controller
from archon.setup.models import SetupRecord


def _blocked_setup(setup_id: str, project_name: str, *, env_var: str = "", what: str = "") -> SetupRecord:
    return SetupRecord(
        setup_id=setup_id,
        project_name=project_name,
        project_path=f"~/Documents/{project_name}",
        status="blocked",
        created_at="2026-03-10T14:30:00Z",
        updated_at="2026-03-10T14:32:00Z",
        summary=f"Waiting for {env_var or what or project_name}",
        steps=[],
        blocked_on=[
            {
                "step_id": 1,
                "what": what or f"Provide {env_var}",
                "hint": "",
                "env_var": env_var,
                "provided": False,
            }
        ],
        resume_hint="Resume setup",
        approval_state="needs_human_input",
    )


def test_extract_job_ref_includes_setup_jobs():
    assert session_controller.extract_job_ref("resume setup:browser-use-20260310") == "setup:browser-use-20260310"


def test_match_blocked_setup_job_requires_single_plausible_match():
    records = [
        _blocked_setup("browser-use-20260310", "browser-use", env_var="OPENAI_API_KEY"),
        _blocked_setup("ai-hedge-fund-20260310", "ai-hedge-fund", env_var="OPENAI_API_KEY"),
    ]

    matched = session_controller.match_blocked_setup_job_for_human_reply(
        "here is OPENAI_API_KEY=sk-test",
        list_records_fn=lambda limit=20: records,
    )

    assert matched == ""


def test_match_blocked_setup_job_returns_only_plausible_match():
    records = [
        _blocked_setup("browser-use-20260310", "browser-use", env_var="OPENAI_API_KEY"),
        _blocked_setup("korami-20260310", "korami-site", env_var="VERCEL_TOKEN"),
    ]

    matched = session_controller.match_blocked_setup_job_for_human_reply(
        "browser-use setup done, here is OPENAI_API_KEY=sk-test",
        list_records_fn=lambda limit=20: records,
    )

    assert matched == "setup:browser-use-20260310"
