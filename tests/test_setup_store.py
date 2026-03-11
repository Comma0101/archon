"""Tests for project setup persistence and formatting."""


def _make_record():
    from archon.setup.models import SetupRecord, SetupStep

    return SetupRecord(
        setup_id="browser-use",
        project_name="browser-use",
        project_path="/tmp/browser-use",
        status="blocked",
        created_at="2026-03-10T14:30:00Z",
        updated_at="2026-03-10T14:32:00Z",
        stack="Python",
        steps=[
            SetupStep(step_id=1, kind="archon", description="Install deps", status="done"),
            SetupStep(
                step_id=2,
                kind="human",
                description="Provide OPENAI_API_KEY",
                status="pending",
                hint="Sign up first",
                env_var="OPENAI_API_KEY",
            ),
            SetupStep(step_id=3, kind="archon", description="Verify install", status="pending"),
        ],
        discovery_sources=["README.md", "pyproject.toml"],
        requirements={"env_vars": ["OPENAI_API_KEY"]},
        generated_skill_path="",
        resume_hint="Provide OPENAI_API_KEY, then continue verification.",
    )


def test_setup_store_save_and_load_roundtrip(monkeypatch, tmp_path):
    from archon.setup.store import load_setup_record, save_setup_record

    monkeypatch.setattr("archon.setup.store.SETUP_RECORDS_DIR", tmp_path / "setup")

    record = _make_record()
    save_setup_record(record)
    loaded = load_setup_record("browser-use")

    assert loaded is not None
    assert loaded.setup_id == "browser-use"
    assert loaded.project_name == "browser-use"
    assert loaded.blocked_steps()[0].env_var == "OPENAI_API_KEY"


def test_setup_job_summary_reflects_blocked_human_steps(monkeypatch, tmp_path):
    from archon.setup.store import list_setup_job_summaries, save_setup_record

    monkeypatch.setattr("archon.setup.store.SETUP_RECORDS_DIR", tmp_path / "setup")

    save_setup_record(_make_record())
    jobs = list_setup_job_summaries(limit=10)

    assert len(jobs) == 1
    assert jobs[0].job_id == "setup:browser-use"
    assert jobs[0].kind == "project_setup"
    assert "waiting for 1 human step(s)" in jobs[0].summary


def test_format_setup_record_shows_blocked_and_pending_steps():
    from archon.setup.formatting import format_setup_record

    text = format_setup_record(_make_record())

    assert "job_id: setup:browser-use" in text
    assert "job_status: blocked" in text
    assert "job_blocked_on:" in text
    assert "Provide OPENAI_API_KEY" in text
    assert "job_pending_steps:" in text
    assert "Verify install" in text
