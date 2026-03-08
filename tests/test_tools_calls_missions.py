"""Tests for call mission Archon tools (Phase 1)."""

import json
from datetime import datetime, timezone

from archon.calls.models import CallMission
from archon.calls.store import call_mission_path, load_call_job_summary, save_call_mission
from archon.config import Config
from archon.safety import Level
from archon.tools import ToolRegistry


def make_registry(confirmer=None):
    return ToolRegistry(archon_source_dir=None, confirmer=confirmer)


class TestCallMissionTools:
    def test_call_mission_schema_descriptions_match_current_runner(self):
        registry = make_registry()
        schemas = {schema["name"]: schema for schema in registry.get_schemas()}
        start_schema = schemas["call_mission_start"]

        assert start_schema["description"] == (
            "Start a voice call mission via the local Archon voice service. "
            "Archon prefers realtime mode when configured and falls back to scripted mode when needed."
        )
        assert start_schema["input_schema"]["properties"]["goal"]["description"] == (
            "Call goal/instructions for the voice mission"
        )

    def test_call_mission_start_requires_approval(self, monkeypatch):
        calls = []

        def fake_confirmer(command, level):
            calls.append((command, level))
            return True

        monkeypatch.setattr(
            "archon.tooling.call_mission_tools.call_runner.start_call_mission",
            lambda **kwargs: {
                "ok": True,
                "call_session_id": "call_1",
                "status": "queued",
                "target_number": kwargs["target_number"],
                "goal": kwargs["goal"],
            },
        )

        registry = make_registry(confirmer=fake_confirmer)
        result = registry.execute(
            "call_mission_start",
            {
                "target_number": "+15551112222",
                "goal": "Call me and ask about my day",
            },
        )

        assert "call_session_id: call_1" in result
        assert calls
        assert calls[0][1] == Level.DANGEROUS

    def test_call_mission_status_reads_store(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "archon.calls.store.CALLS_MISSIONS_DIR",
            tmp_path / "archon" / "calls" / "missions",
        )
        monkeypatch.setattr(
            "archon.calls.store.CALLS_EVENTS_DIR",
            tmp_path / "archon" / "calls" / "events",
        )

        save_call_mission(
            CallMission(
                call_session_id="call_1",
                goal="Call me",
                target_number="+15551112222",
                status="queued",
            )
        )

        registry = make_registry()
        out = registry.execute("call_mission_status", {"call_session_id": "call_1"})

        assert "call_session_id: call_1" in out
        assert "status: queued" in out

    def test_load_call_job_summary_normalizes_mission(self, monkeypatch):
        mission = CallMission(
            call_session_id="call_job_1",
            goal="Call and confirm store hours",
            target_number="+15551112222",
            status="completed",
            updated_at=1708732810.0,
            evaluation_summary="Goal achieved clearly.",
        )
        monkeypatch.setattr(
            "archon.calls.store.load_call_mission",
            lambda sid: mission if sid == "call_job_1" else None,
        )

        job = load_call_job_summary("call_job_1")

        assert job is not None
        assert job.job_id == "call:call_job_1"
        assert job.kind == "call_mission"
        assert job.status == "completed"
        assert job.summary == "Goal achieved clearly."
        assert job.last_update_at == datetime.fromtimestamp(
            1708732810.0, tz=timezone.utc
        ).isoformat().replace("+00:00", "Z")

    def test_call_mission_status_includes_job_summary(self, monkeypatch):
        monkeypatch.setattr(
            "archon.tooling.call_mission_tools.call_runner.call_mission_status",
            lambda call_session_id: {
                "ok": True,
                "call_session_id": call_session_id,
                "status": "queued",
                "goal": "Call me",
            },
        )
        monkeypatch.setattr(
            "archon.tooling.call_mission_tools.load_call_job_summary",
            lambda sid: type(
                "JobSummary",
                (),
                {
                    "to_dict": lambda self: {
                        "job_id": "call:call_job_2",
                        "kind": "call_mission",
                        "status": "queued",
                        "summary": "Call me",
                        "last_update_at": "2026-02-24T00:00:10Z",
                    }
                },
            )(),
        )

        registry = make_registry()
        out = registry.execute("call_mission_status", {"call_session_id": "call_job_2"})

        assert "job_id: call:call_job_2" in out
        assert "job_kind: call_mission" in out
        assert "job_status: queued" in out
        assert "job_summary: Call me" in out
        assert "job_last_update_at: 2026-02-24T00:00:10Z" in out

    def test_call_mission_list_includes_job_summaries(self, monkeypatch):
        monkeypatch.setattr(
            "archon.tooling.call_mission_tools.call_runner.list_call_missions",
            lambda limit=20: {
                "ok": True,
                "count": 1,
                "missions": [
                    {
                        "call_session_id": "call_job_list_1",
                        "status": "completed",
                        "target_number": "+15551112222",
                    }
                ],
            },
        )
        monkeypatch.setattr(
            "archon.tooling.call_mission_tools.list_call_job_summaries",
            lambda limit=20: [
                type(
                    "JobSummary",
                    (),
                    {
                        "to_dict": lambda self: {
                            "job_id": "call:call_job_list_1",
                            "kind": "call_mission",
                            "status": "completed",
                            "summary": "Goal achieved clearly.",
                            "last_update_at": "2026-02-24T00:00:10Z",
                        }
                    },
                )()
            ],
        )

        registry = make_registry()
        out = registry.execute("call_mission_list", {"limit": 5})

        assert "job_summaries:" in out
        assert "call:call_job_list_1" in out
        assert "call_mission" in out
        assert "Goal achieved clearly." in out

    def test_call_mission_status_runner_surfaces_and_persists_realtime_fields(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "archon.calls.store.CALLS_MISSIONS_DIR",
            tmp_path / "archon" / "calls" / "missions",
        )
        monkeypatch.setattr(
            "archon.calls.store.CALLS_EVENTS_DIR",
            tmp_path / "archon" / "calls" / "events",
        )

        save_call_mission(
            CallMission(
                call_session_id="call_rt_1",
                goal="Talk to me",
                target_number="+15551112222",
                status="queued",
            )
        )

        monkeypatch.setattr("archon.calls.runner._active_config", lambda config=None: object())
        monkeypatch.setattr(
            "archon.calls.runner._voice_service_base_url",
            lambda _cfg: "http://127.0.0.1:8788",
        )
        monkeypatch.setattr(
            "archon.calls.runner.service_client.get_call_mission_status",
            lambda **kwargs: {
                "ok": True,
                "status": "in_progress",
                "mission": {
                    "mission_id": "call_rt_1",
                    "status": "in_progress",
                    "mode": "realtime_media_stream",
                    "voice_backend": "deepgram_voice_agent_v1",
                    "think_provider": "open_ai",
                    "think_model": "gpt-4o-mini,gpt-5-mini",
                    "twilio_stream_sid": "MZrt1",
                    "provider_call_sid": "CArt1",
                    "transcript": [
                        {"speaker": "user", "text": "hello there"},
                        {"speaker": "assistant", "text": "hi from archon"},
                    ],
                },
            },
        )

        from archon.calls import runner as call_runner

        result = call_runner.call_mission_status("call_rt_1")

        assert result["ok"] is True
        assert result["status"] == "in_progress"
        assert result["mode"] == "realtime_media_stream"
        assert result["voice_backend"] == "deepgram_voice_agent_v1"
        assert result["think_provider"] == "open_ai"
        assert result["think_model"] == "gpt-4o-mini,gpt-5-mini"
        assert result["twilio_stream_sid"] == "MZrt1"
        assert result["provider_call_sid"] == "CArt1"
        assert result["transcript"][0]["speaker"] == "user"
        assert result["transcript"][0]["text"] == "hello there"

        from archon.tooling.call_mission_tools import _format_result

        formatted = _format_result(result)
        assert "think_provider: open_ai" in formatted
        assert "think_model: gpt-4o-mini,gpt-5-mini" in formatted

        persisted = json.loads(call_mission_path("call_rt_1").read_text(encoding="utf-8"))
        assert persisted["mode"] == "realtime_media_stream"
        assert persisted["voice_backend"] == "deepgram_voice_agent_v1"
        assert persisted["think_provider"] == "open_ai"
        assert persisted["think_model"] == "gpt-4o-mini,gpt-5-mini"
        assert persisted["twilio_stream_sid"] == "MZrt1"
        assert persisted["transcript"][1]["speaker"] == "assistant"

    def test_call_mission_start_prefers_realtime_mode_when_enabled(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "archon.calls.store.CALLS_MISSIONS_DIR",
            tmp_path / "archon" / "calls" / "missions",
        )
        monkeypatch.setattr(
            "archon.calls.store.CALLS_EVENTS_DIR",
            tmp_path / "archon" / "calls" / "events",
        )

        cfg = Config()
        cfg.calls.enabled = True
        cfg.calls.voice_service.base_url = "http://127.0.0.1:8788"
        cfg.calls.realtime.enabled = True
        cfg.calls.realtime.provider = "deepgram_voice_agent_v1"

        submitted_payloads: list[dict] = []
        monkeypatch.setattr(
            "archon.calls.runner.voice_service_health",
            lambda _cfg=None: {"ok": True, "status": "healthy"},
        )

        def fake_submit_call_mission(mission_payload, config=None):
            submitted_payloads.append(dict(mission_payload))
            return {
                "ok": True,
                "status": "queued",
                "mission": {
                    "mission_id": mission_payload["call_session_id"],
                    "mode": mission_payload.get("mode"),
                    "provider_call_sid": "CA_RT_OK",
                },
            }

        monkeypatch.setattr("archon.calls.runner.submit_call_mission", fake_submit_call_mission)

        from archon.calls import runner as call_runner
        from archon.tooling.call_mission_tools import _format_result

        result = call_runner.start_call_mission(
            target_number="+15551112222",
            goal="Call me and ask about my day",
            config=cfg,
        )

        assert result["ok"] is True
        assert result["mode"] == "realtime_media_stream"
        assert result["fallback_used"] is False
        assert submitted_payloads == [
            {
                "call_session_id": result["call_session_id"],
                "goal": "Call me and ask about my day",
                "target_number": "+15551112222",
                "mode": "realtime_media_stream",
            }
        ]

        tool_output = _format_result(result)
        assert "mode: realtime_media_stream" in tool_output
        assert "fallback_used: False" in tool_output

    def test_call_mission_start_falls_back_to_scripted_when_realtime_submit_errors(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            "archon.calls.store.CALLS_MISSIONS_DIR",
            tmp_path / "archon" / "calls" / "missions",
        )
        monkeypatch.setattr(
            "archon.calls.store.CALLS_EVENTS_DIR",
            tmp_path / "archon" / "calls" / "events",
        )

        cfg = Config()
        cfg.calls.enabled = True
        cfg.calls.voice_service.base_url = "http://127.0.0.1:8788"
        cfg.calls.realtime.enabled = True
        cfg.calls.realtime.provider = "deepgram_voice_agent_v1"

        submit_modes: list[str] = []
        monkeypatch.setattr(
            "archon.calls.runner.voice_service_health",
            lambda _cfg=None: {"ok": True, "status": "healthy"},
        )

        def fake_submit_call_mission(mission_payload, config=None):
            mode = str(mission_payload.get("mode") or "scripted_gather")
            submit_modes.append(mode)
            if mode == "realtime_media_stream":
                return {
                    "ok": False,
                    "status": "error",
                    "reason": "realtime backend unavailable",
                }
            return {
                "ok": True,
                "status": "queued",
                "mission": {
                    "mission_id": mission_payload["call_session_id"],
                    "mode": mode,
                    "provider_call_sid": "CA_SCRIPT_OK",
                },
            }

        monkeypatch.setattr("archon.calls.runner.submit_call_mission", fake_submit_call_mission)

        from archon.calls import runner as call_runner
        from archon.tooling.call_mission_tools import _format_result

        result = call_runner.start_call_mission(
            target_number="+15551112222",
            goal="Call me and ask about my day",
            config=cfg,
        )

        assert result["ok"] is True
        assert result["mode"] == "scripted_gather"
        assert result["fallback_used"] is True
        assert submit_modes == ["realtime_media_stream", "scripted_gather"]

        tool_output = _format_result(result)
        assert "mode: scripted_gather" in tool_output
        assert "fallback_used: True" in tool_output

    def test_call_mission_status_evaluates_completed_call_once(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "archon.calls.store.CALLS_MISSIONS_DIR",
            tmp_path / "archon" / "calls" / "missions",
        )
        monkeypatch.setattr(
            "archon.calls.store.CALLS_EVENTS_DIR",
            tmp_path / "archon" / "calls" / "events",
        )

        save_call_mission(
            CallMission(
                call_session_id="call_eval_1",
                goal="Ask the user how their trading session went.",
                target_number="+15551112222",
                status="queued",
            )
        )

        monkeypatch.setattr("archon.calls.runner._active_config", lambda config=None: object())
        monkeypatch.setattr(
            "archon.calls.runner._voice_service_base_url",
            lambda _cfg: "http://127.0.0.1:8788",
        )
        monkeypatch.setattr(
            "archon.calls.runner.service_client.get_call_mission_status",
            lambda **kwargs: {
                "ok": True,
                "status": "completed",
                "mission": {
                    "mission_id": "call_eval_1",
                    "status": "completed",
                    "transcript": [
                        {"speaker": "assistant", "text": "How did trading go today?"},
                        {"speaker": "user", "text": "Pretty good."},
                    ],
                },
            },
        )

        eval_calls: list[tuple[str, str]] = []

        def _fake_eval(goal: str, transcript_text: str, config=None):
            _ = config
            eval_calls.append((goal, transcript_text))
            return {
                "evaluation": "success",
                "evaluation_summary": "Goal achieved clearly.",
                "findings": {"trading_sentiment": "positive"},
                "transcript_summary": "Agent asked about trading performance and user reported positive results.",
            }

        monkeypatch.setattr("archon.calls.runner._evaluate_call_outcome", _fake_eval)

        from archon.calls import runner as call_runner

        first = call_runner.call_mission_status("call_eval_1")
        second = call_runner.call_mission_status("call_eval_1")

        assert first["evaluation"] == "success"
        assert "Goal achieved" in first["evaluation_summary"]
        assert first["findings"] == {"trading_sentiment": "positive"}
        assert "positive results" in first["transcript_summary"]
        assert second["evaluation"] == "success"
        assert len(eval_calls) == 1
        assert eval_calls[0][0] == "Ask the user how their trading session went."
        assert "assistant: How did trading go today?" in eval_calls[0][1]

        persisted = json.loads(call_mission_path("call_eval_1").read_text(encoding="utf-8"))
        assert persisted["evaluation"] == "success"
        assert "Goal achieved" in persisted["evaluation_summary"]
        assert persisted["findings"] == {"trading_sentiment": "positive"}
        assert "positive results" in persisted["transcript_summary"]

    def test_call_mission_status_evaluates_action_goal_with_empty_findings(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "archon.calls.store.CALLS_MISSIONS_DIR",
            tmp_path / "archon" / "calls" / "missions",
        )
        monkeypatch.setattr(
            "archon.calls.store.CALLS_EVENTS_DIR",
            tmp_path / "archon" / "calls" / "events",
        )

        save_call_mission(
            CallMission(
                call_session_id="call_eval_1b",
                goal="Tell the user a joke.",
                target_number="+15551112222",
                status="queued",
            )
        )

        monkeypatch.setattr("archon.calls.runner._active_config", lambda config=None: object())
        monkeypatch.setattr(
            "archon.calls.runner._voice_service_base_url",
            lambda _cfg: "http://127.0.0.1:8788",
        )
        monkeypatch.setattr(
            "archon.calls.runner.service_client.get_call_mission_status",
            lambda **kwargs: {
                "ok": True,
                "status": "completed",
                "mission": {
                    "mission_id": "call_eval_1b",
                    "status": "completed",
                    "transcript": [
                        {"speaker": "assistant", "text": "Why do programmers prefer dark mode?"},
                        {"speaker": "user", "text": "Haha nice one."},
                    ],
                },
            },
        )

        def _fake_eval(goal: str, transcript_text: str, config=None):
            _ = (goal, transcript_text, config)
            return {
                "evaluation": "success",
                "evaluation_summary": "User received and engaged with the joke.",
                "findings": {},
                "transcript_summary": "Agent told a joke and user reacted positively.",
            }

        monkeypatch.setattr("archon.calls.runner._evaluate_call_outcome", _fake_eval)

        from archon.calls import runner as call_runner

        result = call_runner.call_mission_status("call_eval_1b")

        assert result["evaluation"] == "success"
        assert result["findings"] == {}
        assert "joke" in result["transcript_summary"]

    def test_call_mission_status_backfills_missing_findings_without_repeating_after_persist(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            "archon.calls.store.CALLS_MISSIONS_DIR",
            tmp_path / "archon" / "calls" / "missions",
        )
        monkeypatch.setattr(
            "archon.calls.store.CALLS_EVENTS_DIR",
            tmp_path / "archon" / "calls" / "events",
        )

        save_call_mission(
            CallMission(
                call_session_id="call_eval_1c",
                goal="Find out store hours.",
                target_number="+15551112222",
                status="completed",
                evaluation="success",
                evaluation_summary="Goal achieved clearly.",
            )
        )

        monkeypatch.setattr("archon.calls.runner._active_config", lambda config=None: object())
        monkeypatch.setattr(
            "archon.calls.runner._voice_service_base_url",
            lambda _cfg: "http://127.0.0.1:8788",
        )
        monkeypatch.setattr(
            "archon.calls.runner.service_client.get_call_mission_status",
            lambda **kwargs: {
                "ok": True,
                "status": "completed",
                "mission": {
                    "mission_id": "call_eval_1c",
                    "status": "completed",
                    "transcript": [
                        {"speaker": "assistant", "text": "What are your store hours?"},
                        {"speaker": "user", "text": "We are open 9am to 5pm weekdays."},
                    ],
                    "evaluation": "success",
                    "evaluation_summary": "Goal achieved clearly.",
                },
            },
        )

        eval_calls: list[int] = []

        def _fake_eval(goal: str, transcript_text: str, config=None):
            _ = (goal, transcript_text, config)
            eval_calls.append(1)
            return {
                "evaluation": "success",
                "evaluation_summary": "Goal achieved clearly.",
                "findings": {"store_hours": "9am-5pm weekdays"},
                "transcript_summary": "Agent confirmed weekday store hours.",
            }

        monkeypatch.setattr("archon.calls.runner._evaluate_call_outcome", _fake_eval)

        from archon.calls import runner as call_runner

        first = call_runner.call_mission_status("call_eval_1c")
        second = call_runner.call_mission_status("call_eval_1c")

        assert first["findings"] == {"store_hours": "9am-5pm weekdays"}
        assert "weekday store hours" in first["transcript_summary"]
        assert second["findings"] == {"store_hours": "9am-5pm weekdays"}
        assert len(eval_calls) == 1

    def test_call_mission_status_skips_evaluation_when_not_completed(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "archon.calls.store.CALLS_MISSIONS_DIR",
            tmp_path / "archon" / "calls" / "missions",
        )
        monkeypatch.setattr(
            "archon.calls.store.CALLS_EVENTS_DIR",
            tmp_path / "archon" / "calls" / "events",
        )

        save_call_mission(
            CallMission(
                call_session_id="call_eval_2",
                goal="Ask the user how their trading session went.",
                target_number="+15551112222",
                status="queued",
            )
        )

        monkeypatch.setattr("archon.calls.runner._active_config", lambda config=None: object())
        monkeypatch.setattr(
            "archon.calls.runner._voice_service_base_url",
            lambda _cfg: "http://127.0.0.1:8788",
        )
        monkeypatch.setattr(
            "archon.calls.runner.service_client.get_call_mission_status",
            lambda **kwargs: {
                "ok": True,
                "status": "in_progress",
                "mission": {
                    "mission_id": "call_eval_2",
                    "status": "in_progress",
                    "transcript": [{"speaker": "assistant", "text": "How did trading go today?"}],
                },
            },
        )

        called = {"count": 0}

        def _fake_eval(goal: str, transcript_text: str, config=None):
            _ = (goal, transcript_text, config)
            called["count"] += 1
            return {"evaluation": "partial", "evaluation_summary": "unused"}

        monkeypatch.setattr("archon.calls.runner._evaluate_call_outcome", _fake_eval)

        from archon.calls import runner as call_runner

        result = call_runner.call_mission_status("call_eval_2")

        assert result["status"] == "in_progress"
        assert "evaluation" not in result
        assert called["count"] == 0

    def test_call_mission_status_skips_evaluation_when_transcript_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "archon.calls.store.CALLS_MISSIONS_DIR",
            tmp_path / "archon" / "calls" / "missions",
        )
        monkeypatch.setattr(
            "archon.calls.store.CALLS_EVENTS_DIR",
            tmp_path / "archon" / "calls" / "events",
        )

        save_call_mission(
            CallMission(
                call_session_id="call_eval_3",
                goal="Ask the user how their trading session went.",
                target_number="+15551112222",
                status="queued",
            )
        )

        monkeypatch.setattr("archon.calls.runner._active_config", lambda config=None: object())
        monkeypatch.setattr(
            "archon.calls.runner._voice_service_base_url",
            lambda _cfg: "http://127.0.0.1:8788",
        )
        monkeypatch.setattr(
            "archon.calls.runner.service_client.get_call_mission_status",
            lambda **kwargs: {
                "ok": True,
                "status": "completed",
                "mission": {
                    "mission_id": "call_eval_3",
                    "status": "completed",
                    "transcript": [],
                },
            },
        )

        called = {"count": 0}

        def _fake_eval(goal: str, transcript_text: str, config=None):
            _ = (goal, transcript_text, config)
            called["count"] += 1
            return {"evaluation": "partial", "evaluation_summary": "unused"}

        monkeypatch.setattr("archon.calls.runner._evaluate_call_outcome", _fake_eval)

        from archon.calls import runner as call_runner

        result = call_runner.call_mission_status("call_eval_3")

        assert result["status"] == "completed"
        assert "evaluation" not in result
        assert called["count"] == 0

    def test_call_mission_status_evaluation_failure_is_best_effort(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "archon.calls.store.CALLS_MISSIONS_DIR",
            tmp_path / "archon" / "calls" / "missions",
        )
        monkeypatch.setattr(
            "archon.calls.store.CALLS_EVENTS_DIR",
            tmp_path / "archon" / "calls" / "events",
        )

        save_call_mission(
            CallMission(
                call_session_id="call_eval_4",
                goal="Ask the user how their trading session went.",
                target_number="+15551112222",
                status="queued",
            )
        )

        monkeypatch.setattr("archon.calls.runner._active_config", lambda config=None: object())
        monkeypatch.setattr(
            "archon.calls.runner._voice_service_base_url",
            lambda _cfg: "http://127.0.0.1:8788",
        )
        monkeypatch.setattr(
            "archon.calls.runner.service_client.get_call_mission_status",
            lambda **kwargs: {
                "ok": True,
                "status": "completed",
                "mission": {
                    "mission_id": "call_eval_4",
                    "status": "completed",
                    "transcript": [
                        {"speaker": "assistant", "text": "How did trading go today?"},
                        {"speaker": "user", "text": "Pretty good."},
                    ],
                },
            },
        )

        def _fake_eval(goal: str, transcript_text: str, config=None):
            _ = (goal, transcript_text, config)
            raise RuntimeError("llm down")

        monkeypatch.setattr("archon.calls.runner._evaluate_call_outcome", _fake_eval)

        from archon.calls import runner as call_runner

        result = call_runner.call_mission_status("call_eval_4")

        assert result["status"] == "completed"
        assert "evaluation" not in result

    def test_call_mission_format_result_includes_evaluation_fields(self):
        from archon.tooling.call_mission_tools import _format_result

        output = _format_result(
            {
                "ok": True,
                "call_session_id": "call_eval_5",
                "status": "completed",
                "evaluation": "success",
                "evaluation_summary": "Goal achieved clearly.",
                "findings": {"store_hours": "9am-5pm"},
                "transcript_summary": "Agent asked for store hours and captured the answer.",
            }
        )

        assert "evaluation: success" in output
        assert "evaluation_summary: Goal achieved clearly." in output
        assert "findings:" in output
        assert "store_hours: 9am-5pm" in output
        assert "transcript_summary: Agent asked for store hours" in output
