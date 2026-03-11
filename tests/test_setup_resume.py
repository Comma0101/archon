"""Resume matching for blocked setup jobs."""


def _make_blocked_record(setup_id: str, project: str, env_var: str, hint: str = ""):
    from archon.setup.models import SetupRecord, SetupStep

    return SetupRecord(
        setup_id=setup_id,
        project_name=project,
        project_path=f"/tmp/{project}",
        status="blocked",
        created_at="",
        updated_at="",
        stack="",
        steps=[
            SetupStep(step_id=1, kind="archon", description="Install", status="done"),
            SetupStep(
                step_id=2,
                kind="human",
                description=f"Provide {env_var}",
                status="pending",
                hint=hint,
                env_var=env_var,
            ),
        ],
        discovery_sources=[],
        requirements={},
        generated_skill_path="",
        resume_hint="",
    )


def test_match_input_to_blocked_job_returns_no_blocked_jobs_when_empty():
    from archon.setup.resume import match_input_to_blocked_job

    result = match_input_to_blocked_job("here's a key", [])

    assert result.kind == "no_blocked_jobs"


def test_match_input_to_blocked_job_returns_single_match_for_named_project():
    from archon.setup.resume import match_input_to_blocked_job

    records = [
        _make_blocked_record("s1", "browser-use", "OPENAI_API_KEY"),
        _make_blocked_record("s2", "hedge-fund", "ALPACA_KEY"),
    ]

    result = match_input_to_blocked_job("browser-use API key is sk-abc", records)

    assert result.kind == "single_match"
    assert result.job is not None
    assert result.job.setup_id == "s1"


def test_match_input_to_blocked_job_returns_ambiguous_for_generic_key_message():
    from archon.setup.resume import match_input_to_blocked_job

    records = [
        _make_blocked_record("s1", "project-a", "API_KEY"),
        _make_blocked_record("s2", "project-b", "API_KEY"),
    ]

    result = match_input_to_blocked_job("here is the API key", records)

    assert result.kind == "ambiguous"
    assert [item.setup_id for item in result.candidates] == ["s1", "s2"]


def test_match_input_to_blocked_job_returns_no_match_for_irrelevant_message():
    from archon.setup.resume import match_input_to_blocked_job

    records = [_make_blocked_record("s1", "browser-use", "OPENAI_API_KEY")]

    result = match_input_to_blocked_job("what's the weather today", records)

    assert result.kind == "no_match"
