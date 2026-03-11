from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from archon.control.contracts import HookEvent
from archon.ux.events import job_progress


def _load_smoke_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "deep_research_smoke.py"
    spec = importlib.util.spec_from_file_location("deep_research_smoke_test_module", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_smoke_fails_without_api_key(monkeypatch, capsys):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    smoke = _load_smoke_module()
    monkeypatch.setattr(
        smoke,
        "_load_cfg",
        lambda: SimpleNamespace(
            llm=SimpleNamespace(
                provider="anthropic",
                api_key="",
                base_url="",
                fallback_provider="openai",
                fallback_api_key="",
                fallback_base_url="",
            ),
            research=SimpleNamespace(),
        ),
    )

    exit_code = smoke.main(["--prompt", "test prompt", "--timeout", "1"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "Missing Google API key" in captured.err


def test_smoke_succeeds_when_stream_evidence_is_observed(monkeypatch, capsys):
    smoke = _load_smoke_module()
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(smoke, "_sleep", lambda _seconds: None)
    monkeypatch.setattr(smoke, "_load_cfg", lambda: SimpleNamespace(research=SimpleNamespace()))

    class _FakeClientFactory:
        @staticmethod
        def from_api_key(api_key: str, *, agent: str, thinking_summaries: str):
            return SimpleNamespace(api_key=api_key, agent=agent, thinking_summaries=thinking_summaries)

    records = [
        SimpleNamespace(
            interaction_id="job-123",
            status="in_progress",
            provider_status="in_progress",
            stream_status="content.delta",
            last_event_id="evt-1",
            latest_thought_summary="Checking sources",
            event_count=1,
            poll_count=0,
            error="",
        )
    ]

    def _fake_start(prompt: str, *, client, agent_name: str, timeout_minutes: int, hook_bus=None, **_kwargs):
        assert prompt == "test prompt"
        assert client.api_key == "test-key"
        assert agent_name
        hook_bus.emit(
            HookEvent(
                kind="ux.job_progress",
                payload={"event": job_progress(job_kind="research", job_id="research:job-123", status="in_progress", summary="Checking sources")},
            )
        )
        return SimpleNamespace(interaction_id="job-123")

    def _fake_load(interaction_id: str):
        assert interaction_id == "job-123"
        return records[0]

    monkeypatch.setattr(smoke, "GoogleDeepResearchClient", _FakeClientFactory)
    monkeypatch.setattr(smoke, "start_research_stream_job", _fake_start)
    monkeypatch.setattr(smoke, "load_research_job", _fake_load)

    exit_code = smoke.main(["--prompt", "test prompt", "--timeout", "1"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "research:job-123" in captured.out
    assert "Checking sources" in captured.out
    assert "latest_thought_summary: Checking sources" in captured.out


def test_smoke_uses_google_key_from_config_when_env_is_empty(monkeypatch, capsys):
    smoke = _load_smoke_module()
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr(smoke, "_sleep", lambda _seconds: None)
    monkeypatch.setattr(
        smoke,
        "_load_cfg",
        lambda: SimpleNamespace(
            llm=SimpleNamespace(
                provider="google",
                api_key="cfg-key",
                base_url="",
                fallback_provider="openai",
                fallback_api_key="",
                fallback_base_url="",
            ),
            research=SimpleNamespace(),
        ),
    )

    class _FakeClientFactory:
        @staticmethod
        def from_api_key(api_key: str, *, agent: str, thinking_summaries: str):
            return SimpleNamespace(api_key=api_key, agent=agent, thinking_summaries=thinking_summaries)

    monkeypatch.setattr(smoke, "GoogleDeepResearchClient", _FakeClientFactory)
    monkeypatch.setattr(
        smoke,
        "start_research_stream_job",
        lambda *args, **kwargs: SimpleNamespace(interaction_id="job-789"),
    )
    monkeypatch.setattr(
        smoke,
        "load_research_job",
        lambda _interaction_id: SimpleNamespace(
            interaction_id="job-789",
            status="in_progress",
            provider_status="in_progress",
            stream_status="content.delta",
            last_event_id="evt-1",
            latest_thought_summary="Checking sources",
            event_count=1,
            poll_count=0,
            error="",
        ),
    )

    exit_code = smoke.main(["--prompt", "test prompt", "--timeout", "1"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "started: research:job-789" in captured.out


def test_smoke_waits_for_timeout_before_final_snapshot(monkeypatch, capsys):
    smoke = _load_smoke_module()
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(smoke, "_load_cfg", lambda: SimpleNamespace(research=SimpleNamespace()))

    clock = {"now": 0.0}

    def _fake_sleep(seconds: float) -> None:
        clock["now"] += seconds

    monkeypatch.setattr(smoke, "_sleep", _fake_sleep)
    monkeypatch.setattr(smoke.time, "monotonic", lambda: clock["now"])

    class _FakeClientFactory:
        @staticmethod
        def from_api_key(api_key: str, *, agent: str, thinking_summaries: str):
            return SimpleNamespace(api_key=api_key, agent=agent, thinking_summaries=thinking_summaries)

    early = SimpleNamespace(
        interaction_id="job-999",
        status="in_progress",
        provider_status="in_progress",
        stream_status="interaction.status_update",
        last_event_id="",
        latest_thought_summary="",
        event_count=2,
        poll_count=0,
        error="",
    )
    later = SimpleNamespace(
        interaction_id="job-999",
        status="in_progress",
        provider_status="in_progress",
        stream_status="content.delta",
        last_event_id="evt-1",
        latest_thought_summary="Checking sources",
        event_count=3,
        poll_count=0,
        error="",
    )
    def _fake_load(_interaction_id: str):
        return later if clock["now"] >= 0.5 else early

    monkeypatch.setattr(smoke, "GoogleDeepResearchClient", _FakeClientFactory)
    monkeypatch.setattr(
        smoke,
        "start_research_stream_job",
        lambda *args, **kwargs: SimpleNamespace(interaction_id="job-999"),
    )
    monkeypatch.setattr(smoke, "load_research_job", _fake_load)

    exit_code = smoke.main(["--prompt", "test prompt", "--timeout", "1"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "last_event_id: evt-1" in captured.out
    assert "latest_thought_summary: Checking sources" in captured.out


def test_smoke_fails_when_timeout_expires_without_stream_evidence(monkeypatch, capsys):
    smoke = _load_smoke_module()
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(smoke, "_sleep", lambda _seconds: None)
    monkeypatch.setattr(smoke, "_load_cfg", lambda: SimpleNamespace(research=SimpleNamespace()))

    class _FakeClientFactory:
        @staticmethod
        def from_api_key(api_key: str, *, agent: str, thinking_summaries: str):
            return SimpleNamespace(api_key=api_key, agent=agent, thinking_summaries=thinking_summaries)

    record = SimpleNamespace(
        interaction_id="job-456",
        status="in_progress",
        provider_status="in_progress",
        stream_status="interaction.status_update",
        last_event_id="",
        latest_thought_summary="",
        event_count=0,
        poll_count=0,
        error="",
    )

    monkeypatch.setattr(smoke, "GoogleDeepResearchClient", _FakeClientFactory)
    monkeypatch.setattr(
        smoke,
        "start_research_stream_job",
        lambda *args, **kwargs: SimpleNamespace(interaction_id="job-456"),
    )
    monkeypatch.setattr(smoke, "load_research_job", lambda _interaction_id: record)

    exit_code = smoke.main(["--prompt", "test prompt", "--timeout", "1"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "No Deep Research stream evidence observed" in captured.err
