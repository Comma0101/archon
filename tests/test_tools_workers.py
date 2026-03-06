"""Tests for tool registry and built-in tools."""

import os
import tempfile
from pathlib import Path

from archon.control.jobs import job_summary_from_worker_record
from archon.config import Config
from archon.execution.runner import run_task
from archon.news.models import NewsDigest
from archon.news.models import NewsRunResult
from archon.tools import ToolRegistry
from archon.workers.base import WorkerEvent, WorkerResult, WorkerTask
from archon.workers.runtime import ActiveWorkerRun
from archon.workers.session_store import (
    WorkerSessionRecord,
    load_worker_job_summary,
)


def make_registry():
    return ToolRegistry(archon_source_dir=None)


class TestExecutionRunner:
    def test_host_backend_routes_to_legacy_bridge(self, monkeypatch, tmp_path):
        captured = {}
        task = WorkerTask(
            task="Review this repo",
            worker="auto",
            mode="review",
            repo_path=str(tmp_path),
            timeout_sec=120,
            constraints="Read-only",
        )

        def fake_bridge(in_task, exec_observer=None):
            captured["task"] = in_task
            captured["exec_observer"] = exec_observer
            return WorkerResult(
                worker="codex",
                status="ok",
                summary="Bridge executed",
                repo_path=in_task.repo_path,
            )

        monkeypatch.setattr("archon.execution.runner.run_worker_task_legacy", fake_bridge)

        result = run_task(task, execution_backend="host")

        assert result.status == "ok"
        assert result.summary == "Bridge executed"
        assert captured["task"] == task
        assert captured["exec_observer"] is None

    def test_unknown_backend_returns_unsupported(self, tmp_path):
        task = WorkerTask(
            task="Review this repo",
            worker="auto",
            mode="review",
            repo_path=str(tmp_path),
            timeout_sec=120,
            constraints="Read-only",
        )

        result = run_task(task, execution_backend="weird-backend")

        assert result.status == "unsupported"
        assert "Unsupported execution backend" in result.summary

    def test_worker_tools_imports_execution_runner_alias(self):
        from archon.tooling import worker_tools

        assert worker_tools.run_worker_task.__module__ == "archon.execution.runner"


class TestSessionControllerExtraction:
    def test_worker_tools_execution_mode_wrapper_uses_session_controller(self, monkeypatch):
        from archon.tooling import worker_tools

        monkeypatch.setattr(
            "archon.tooling.worker_tools.session_controller.choose_delegate_execution_mode",
            lambda **kwargs: ("background", "from_test"),
        )

        mode, reason = worker_tools._choose_delegate_execution_mode(
            task="review repo",
            mode="review",
            timeout_sec=100,
            requested_execution_mode="auto",
        )

        assert mode == "background"
        assert reason == "from_test"

    def test_worker_tools_find_latest_wrapper_passes_list_sessions(self, monkeypatch, tmp_path):
        from archon.tooling import worker_tools

        captured = {}
        sentinel = object()

        def fake_find_latest_worker_session_for_repo(*, worker, repo_path, list_sessions_fn):
            captured["worker"] = worker
            captured["repo_path"] = repo_path
            captured["list_sessions_fn"] = list_sessions_fn
            return sentinel

        monkeypatch.setattr(
            "archon.tooling.worker_tools.session_controller.find_latest_worker_session_for_repo",
            fake_find_latest_worker_session_for_repo,
        )

        result = worker_tools._find_latest_worker_session_for_repo(
            worker="opencode",
            repo_path=str(tmp_path),
        )

        assert result is sentinel
        assert captured["worker"] == "opencode"
        assert captured["repo_path"] == str(tmp_path)
        assert captured["list_sessions_fn"] is worker_tools.list_worker_sessions


class TestDelegateCodeTask:
    def test_delegates_to_worker_backend(self, monkeypatch, tmp_path):
        reg = make_registry()
        captured = {}

        def fake_run(task):
            captured["task"] = task
            return WorkerResult(
                worker="codex",
                status="ok",
                summary="Completed delegated task",
                repo_path=task.repo_path,
                command=["codex", "exec"],
                exit_code=0,
                final_message="All done",
            )

        monkeypatch.setattr(
            "archon.tooling.worker_tools.record_worker_run",
            lambda task, result, requested_worker: WorkerSessionRecord(
                session_id="sess-123",
                created_at="2026-02-24T00:00:00Z",
                updated_at="2026-02-24T00:00:00Z",
                completed_at="2026-02-24T00:00:00Z",
                requested_worker=requested_worker,
                selected_worker=result.worker,
                mode=task.mode,
                status=result.status,
                repo_path=task.repo_path,
                task=task.task,
                constraints=task.constraints,
                timeout_sec=task.timeout_sec,
                summary=result.summary,
                exit_code=result.exit_code,
                error=result.error,
                vendor_session_id=result.vendor_session_id,
                event_count=len(result.events),
            ),
        )
        monkeypatch.setattr("archon.tooling.worker_tools.run_worker_task", fake_run)

        result = reg.execute(
            "delegate_code_task",
            {
                "task": "Review the repository for flaky tests",
                "mode": "review",
                "repo_path": str(tmp_path),
                "worker": "auto",
                "constraints": "Do not modify files.",
            },
        )

        assert "archon_session_id: sess-123" in result
        assert "worker: codex" in result
        assert "status: ok" in result
        assert "final_message:" in result
        assert captured["task"].mode == "review"
        assert captured["task"].repo_path == str(tmp_path.resolve())
        assert "Do not modify files." in captured["task"].constraints

    def test_dangerous_delegate_respects_confirmer(self, tmp_path):
        calls = []

        def deny_all(command, level):
            calls.append((command, level))
            return False

        reg = ToolRegistry(archon_source_dir=None, confirmer=deny_all)
        result = reg.execute(
            "delegate_code_task",
            {
                "task": "Implement feature X",
                "mode": "implement",
                "repo_path": str(tmp_path),
            },
        )

        assert "rejected by safety gate" in result.lower()
        assert calls

    def test_deep_review_auto_backgrounds(self, monkeypatch, tmp_path):
        reg = ToolRegistry(archon_source_dir=None, confirmer=lambda _c, _l: True)

        monkeypatch.setattr(
            "archon.tooling.worker_tools.start_background_worker",
            lambda task, requested_worker: ActiveWorkerRun(
                session_id="sess-deep",
                requested_worker=requested_worker,
                state="starting",
                started_at="2026-02-24T00:00:00Z",
                updated_at="2026-02-24T00:00:00Z",
                thread_name="archon-worker-sessdeep",
            ),
        )

        result = reg.execute(
            "delegate_code_task",
            {
                "task": "Do a deep comprehensive review of this repository and help me understand the whole project",
                "mode": "review",
                "repo_path": str(tmp_path),
                "worker": "opencode",
            },
        )
        assert "execution_mode: background" in result
        assert "execution_reason: deep_scope_request" in result
        assert "background: started" in result
        assert "archon_session_id: sess-deep" in result

    def test_explicit_oneshot_overrides_auto_background(self, monkeypatch, tmp_path):
        reg = ToolRegistry(archon_source_dir=None, confirmer=lambda _c, _l: True)
        captured = {}

        monkeypatch.setattr(
            "archon.tooling.worker_tools.reserve_worker_session",
            lambda task, requested_worker: WorkerSessionRecord(
                session_id="sess-oneshot",
                created_at="2026-02-24T00:00:00Z",
                updated_at="2026-02-24T00:00:00Z",
                completed_at="",
                requested_worker=requested_worker,
                selected_worker="",
                mode=task.mode,
                status="running",
                repo_path=task.repo_path,
                task=task.task,
                constraints=task.constraints,
                timeout_sec=task.timeout_sec,
                summary="reserved",
                exit_code=None,
                error="",
                event_count=0,
                turn_count=0,
            ),
        )

        def fake_run(task):
            captured["task"] = task
            return WorkerResult(
                worker="opencode",
                status="ok",
                summary="Done",
                repo_path=task.repo_path,
                final_message="Review complete",
            )

        monkeypatch.setattr("archon.tooling.worker_tools.run_worker_task", fake_run)
        monkeypatch.setattr(
            "archon.tooling.worker_tools.record_worker_run",
            lambda task, result, requested_worker: WorkerSessionRecord(
                session_id="sess-oneshot",
                created_at="2026-02-24T00:00:00Z",
                updated_at="2026-02-24T00:00:10Z",
                completed_at="2026-02-24T00:00:10Z",
                requested_worker=requested_worker,
                selected_worker=result.worker,
                mode=task.mode,
                status=result.status,
                repo_path=task.repo_path,
                task=task.task,
                constraints=task.constraints,
                timeout_sec=task.timeout_sec,
                summary=result.summary,
                exit_code=result.exit_code,
                error=result.error,
                vendor_session_id=result.vendor_session_id,
                event_count=len(result.events),
            ),
        )

        result = reg.execute(
            "delegate_code_task",
            {
                "task": "Deep review the whole project and explain architecture",
                "mode": "review",
                "repo_path": str(tmp_path),
                "worker": "opencode",
                "execution_mode": "oneshot",
            },
        )
        assert "execution_mode: oneshot" in result
        assert "execution_reason: explicit_request" in result
        assert "background: started" not in result
        assert captured["task"].worker == "opencode"

    def test_invalid_execution_mode_rejected(self, tmp_path):
        reg = make_registry()
        result = reg.execute(
            "delegate_code_task",
            {
                "task": "Review repo",
                "mode": "review",
                "repo_path": str(tmp_path),
                "execution_mode": "weird",
            },
        )
        assert "invalid execution_mode" in result.lower()

    def test_delegate_auto_reroutes_continue_same_opencode_session(self, monkeypatch, tmp_path):
        reg = ToolRegistry(archon_source_dir=None, confirmer=lambda _c, _l: True)
        captured = {}
        base_record = WorkerSessionRecord(
            session_id="sess-existing",
            created_at="2026-02-24T00:00:00Z",
            updated_at="2026-02-24T00:01:00Z",
            completed_at="2026-02-24T00:01:00Z",
            requested_worker="opencode",
            selected_worker="opencode",
            mode="review",
            status="ok",
            repo_path=str(tmp_path),
            task="Initial review",
            constraints="Read-only analysis.",
            timeout_sec=900,
            summary="Initial summary",
            exit_code=0,
            error="",
            vendor_session_id="oc-123",
            event_count=3,
            turn_count=1,
        )
        base_task = WorkerTask(
            task="Initial review",
            worker="opencode",
            mode="review",
            repo_path=str(tmp_path),
            timeout_sec=900,
            constraints="Read-only analysis.",
            model="",
        )

        monkeypatch.setattr("archon.tooling.worker_tools.list_worker_sessions", lambda limit=100: [base_record])
        monkeypatch.setattr("archon.tooling.worker_tools.get_background_run", lambda sid: None)
        monkeypatch.setattr("archon.tooling.worker_tools.load_worker_session", lambda sid: base_record if sid == "sess-existing" else None)
        monkeypatch.setattr("archon.tooling.worker_tools.load_worker_task", lambda sid: base_task if sid == "sess-existing" else None)
        monkeypatch.setattr("archon.tooling.worker_tools.list_worker_approvals", lambda sid, pending_only=True: [])

        def fake_run(task):
            captured["task"] = task
            return WorkerResult(
                worker="opencode",
                status="ok",
                summary="Follow-up complete",
                repo_path=str(tmp_path),
                final_message="Continued successfully",
                vendor_session_id="oc-123",
                events=[WorkerEvent(kind="result", payload={"type": "result"})],
            )

        monkeypatch.setattr("archon.tooling.worker_tools.run_worker_task", fake_run)
        monkeypatch.setattr(
            "archon.tooling.worker_tools.append_worker_turn",
            lambda sid, task, result: WorkerSessionRecord(
                session_id="sess-existing",
                created_at=base_record.created_at,
                updated_at="2026-02-24T00:02:00Z",
                completed_at="2026-02-24T00:02:00Z",
                requested_worker="opencode",
                selected_worker="opencode",
                mode="review",
                status="ok",
                repo_path=str(tmp_path),
                task=base_record.task,
                constraints=base_record.constraints,
                timeout_sec=900,
                summary="Follow-up complete",
                exit_code=0,
                error="",
                vendor_session_id="oc-123",
                event_count=4,
                turn_count=2,
            ),
        )
        monkeypatch.setattr(
            "archon.tooling.worker_tools.reserve_worker_session",
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not start a new delegation")),
        )

        result = reg.execute(
            "delegate_code_task",
            {
                "task": "Continue with the same opencode session and focus on deployment details",
                "worker": "opencode",
                "mode": "review",
                "repo_path": str(tmp_path),
            },
        )
        assert "session_reuse: auto_continue_latest" in result
        assert "matched_archon_session_id: sess-existing" in result
        assert "selected_worker: opencode" in result
        assert captured["task"].resume_vendor_session_id == "oc-123"

    def test_delegate_defaults_to_sticky_opencode_session_reuse(self, monkeypatch, tmp_path):
        reg = ToolRegistry(archon_source_dir=None, confirmer=lambda _c, _l: True)
        captured = {}
        base_record = WorkerSessionRecord(
            session_id="sess-sticky",
            created_at="2026-02-24T00:00:00Z",
            updated_at="2026-02-24T00:01:00Z",
            completed_at="2026-02-24T00:01:00Z",
            requested_worker="opencode",
            selected_worker="opencode",
            mode="review",
            status="ok",
            repo_path=str(tmp_path),
            task="Initial review",
            constraints="Read-only analysis.",
            timeout_sec=900,
            summary="Initial summary",
            exit_code=0,
            error="",
            vendor_session_id="oc-sticky",
            event_count=3,
            turn_count=1,
        )
        base_task = WorkerTask(
            task="Initial review",
            worker="opencode",
            mode="review",
            repo_path=str(tmp_path),
            timeout_sec=900,
            constraints="Read-only analysis.",
            model="",
        )
        reg._set_worker_session_affinity("sess-sticky", str(tmp_path), "opencode")

        monkeypatch.setattr("archon.tooling.worker_tools.load_worker_session", lambda sid: base_record if sid == "sess-sticky" else None)
        monkeypatch.setattr("archon.tooling.worker_tools.get_background_run", lambda sid: None)
        monkeypatch.setattr("archon.tooling.worker_tools.load_worker_task", lambda sid: base_task if sid == "sess-sticky" else None)
        monkeypatch.setattr("archon.tooling.worker_tools.list_worker_approvals", lambda sid, pending_only=True: [])

        def fake_start_background(task, requested_worker):
            captured["task"] = task
            return ActiveWorkerRun(
                session_id="sess-sticky",
                requested_worker=requested_worker,
                state="starting",
                started_at="2026-02-24T00:00:00Z",
                updated_at="2026-02-24T00:00:00Z",
                thread_name="archon-worker-sesssticky",
            )

        monkeypatch.setattr("archon.tooling.worker_tools.start_background_worker", fake_start_background)
        monkeypatch.setattr(
            "archon.tooling.worker_tools.reserve_worker_session",
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not start new session")),
        )

        result = reg.execute(
            "delegate_code_task",
            {
                "task": "Now focus on deployment details and env vars",
                "worker": "opencode",
                "mode": "implement",
                "repo_path": str(tmp_path),
            },
        )
        assert "session_reuse: reused_sticky_session" in result
        assert "matched_archon_session_id: sess-sticky" in result
        assert "selected_worker: opencode" in result
        assert "background: started" in result
        assert captured["task"].resume_vendor_session_id == "oc-sticky"
        assert captured["task"].mode == "implement"

    def test_delegate_explicit_new_session_bypasses_sticky_reuse(self, monkeypatch, tmp_path):
        reg = ToolRegistry(archon_source_dir=None, confirmer=lambda _c, _l: True)
        reg._set_worker_session_affinity("sess-old", str(tmp_path), "opencode")

        monkeypatch.setattr(
            "archon.tooling.worker_tools.start_background_worker",
            lambda task, requested_worker: ActiveWorkerRun(
                session_id="sess-new",
                requested_worker=requested_worker,
                state="starting",
                started_at="2026-02-24T00:00:00Z",
                updated_at="2026-02-24T00:00:00Z",
                thread_name="archon-worker-sessnew",
            ),
        )
        monkeypatch.setattr(
            "archon.tooling.worker_tools.load_worker_session",
            lambda sid: (_ for _ in ()).throw(AssertionError("sticky session should not be loaded for explicit new")),
        )

        result = reg.execute(
            "delegate_code_task",
            {
                "task": "Start a new opencode session and review this repo deeply",
                "worker": "opencode",
                "mode": "review",
                "repo_path": str(tmp_path),
            },
        )
        assert "session_policy: explicit_new_session" in result
        assert "background: started" in result
        assert "archon_session_id: sess-new" in result

    def test_worker_status_tool_formats_session(self, monkeypatch):
        reg = make_registry()
        record = WorkerSessionRecord(
            session_id="sess-1",
            created_at="2026-02-24T00:00:00Z",
            updated_at="2026-02-24T00:00:10Z",
            completed_at="2026-02-24T00:00:10Z",
            requested_worker="auto",
            selected_worker="codex",
            mode="review",
            status="ok",
            repo_path="/tmp/repo",
            task="Review code",
            constraints="",
            timeout_sec=900,
            summary="Looks good",
            exit_code=0,
            error="",
            event_count=1,
        )
        monkeypatch.setattr("archon.tooling.worker_tools.load_worker_session", lambda sid: record if sid == "sess-1" else None)
        monkeypatch.setattr(
            "archon.tooling.worker_tools.load_worker_result",
            lambda sid: WorkerResult(
                worker="codex",
                status="ok",
                summary="Looks good",
                repo_path="/tmp/repo",
                final_message="Final review message",
            ),
        )
        monkeypatch.setattr("archon.tooling.worker_tools.load_worker_events", lambda sid, limit=25: [])
        monkeypatch.setattr("archon.tooling.worker_tools.list_worker_approvals", lambda sid, pending_only=True: [])

        result = reg.execute("worker_status", {"session_id": "sess-1"})
        assert "archon_session_id: sess-1" in result
        assert "selected_worker: codex" in result
        assert "effective_worker: codex" in result
        assert "Final review message" in result
        assert "job_id: worker:sess-1" in result
        assert "job_kind: worker_session" in result
        assert "job_status: ok" in result
        assert "job_summary: Looks good" in result
        assert "job_last_update_at: 2026-02-24T00:00:10Z" in result

    def test_worker_job_summary_normalizes_record_fields(self):
        record = WorkerSessionRecord(
            session_id="sess-job",
            created_at="2026-02-24T00:00:00Z",
            updated_at="2026-02-24T00:00:10Z",
            completed_at="2026-02-24T00:00:10Z",
            requested_worker="auto",
            selected_worker="codex",
            mode="review",
            status="ok",
            repo_path="/tmp/repo",
            task="Review code",
            constraints="",
            timeout_sec=900,
            summary="Looks good",
            exit_code=0,
            error="",
            event_count=1,
        )

        job = job_summary_from_worker_record(record)

        assert job.job_id == "worker:sess-job"
        assert job.kind == "worker_session"
        assert job.status == "ok"
        assert job.summary == "Looks good"
        assert job.last_update_at == "2026-02-24T00:00:10Z"

    def test_worker_status_shows_effective_worker_when_selected_blank(self, monkeypatch):
        reg = make_registry()
        record = WorkerSessionRecord(
            session_id="sess-running",
            created_at="2026-02-24T00:00:00Z",
            updated_at="2026-02-24T00:00:10Z",
            completed_at="",
            requested_worker="opencode",
            selected_worker="",
            mode="review",
            status="running",
            repo_path="/tmp/repo",
            task="Deep review",
            constraints="",
            timeout_sec=900,
            summary="Worker session reserved",
            exit_code=None,
            error="",
            event_count=0,
            turn_count=0,
        )
        monkeypatch.setattr("archon.tooling.worker_tools.load_worker_session", lambda sid: record if sid == "sess-running" else None)
        monkeypatch.setattr("archon.tooling.worker_tools.load_worker_result", lambda sid: None)
        monkeypatch.setattr("archon.tooling.worker_tools.load_worker_events", lambda sid, limit=25: [])
        monkeypatch.setattr("archon.tooling.worker_tools.list_worker_approvals", lambda sid, pending_only=True: [])

        result = reg.execute("worker_status", {"session_id": "sess-running"})
        assert "selected_worker: " in result
        assert "effective_worker: opencode" in result

    def test_load_worker_job_summary_normalizes_session_record(self, monkeypatch):
        record = WorkerSessionRecord(
            session_id="sess-job",
            created_at="2026-02-24T00:00:00Z",
            updated_at="2026-02-24T00:00:10Z",
            completed_at="2026-02-24T00:00:10Z",
            requested_worker="auto",
            selected_worker="codex",
            mode="review",
            status="ok",
            repo_path="/tmp/repo",
            task="Review code",
            constraints="",
            timeout_sec=900,
            summary="Looks good",
            exit_code=0,
            error="",
        )
        monkeypatch.setattr(
            "archon.workers.session_store.load_worker_session",
            lambda sid: record if sid == "sess-job" else None,
        )

        job = load_worker_job_summary("sess-job")

        assert job is not None
        assert job.job_id == "worker:sess-job"
        assert job.kind == "worker_session"
        assert job.status == "ok"
        assert job.summary == "Looks good"
        assert job.last_update_at == "2026-02-24T00:00:10Z"

    def test_worker_status_includes_job_summary_block(self, monkeypatch):
        reg = make_registry()
        record = WorkerSessionRecord(
            session_id="sess-job",
            created_at="2026-02-24T00:00:00Z",
            updated_at="2026-02-24T00:00:10Z",
            completed_at="2026-02-24T00:00:10Z",
            requested_worker="auto",
            selected_worker="codex",
            mode="review",
            status="ok",
            repo_path="/tmp/repo",
            task="Review code",
            constraints="",
            timeout_sec=900,
            summary="Looks good",
            exit_code=0,
            error="",
            event_count=1,
        )
        monkeypatch.setattr("archon.tooling.worker_tools.load_worker_session", lambda sid: record if sid == "sess-job" else None)
        monkeypatch.setattr("archon.tooling.worker_tools.load_worker_result", lambda sid: None)
        monkeypatch.setattr("archon.tooling.worker_tools.load_worker_events", lambda sid, limit=25: [])
        monkeypatch.setattr("archon.tooling.worker_tools.list_worker_approvals", lambda sid, pending_only=True: [])

        result = reg.execute("worker_status", {"session_id": "sess-job"})

        assert "job_id: worker:sess-job" in result
        assert "job_kind: worker_session" in result
        assert "job_status: ok" in result
        assert "job_summary: Looks good" in result
        assert "job_last_update_at: 2026-02-24T00:00:10Z" in result

    def test_worker_list_tool_formats_records(self, monkeypatch):
        reg = make_registry()
        records = [
            WorkerSessionRecord(
                session_id="sess-2",
                created_at="2026-02-24T00:00:00Z",
                updated_at="2026-02-24T00:00:10Z",
                completed_at="2026-02-24T00:00:10Z",
                requested_worker="auto",
                selected_worker="claude_code",
                mode="analyze",
                status="ok",
                repo_path="/tmp/repo",
                task="Analyze code",
                constraints="",
                timeout_sec=300,
                summary="Done",
                exit_code=0,
                error="",
                event_count=2,
            )
        ]
        monkeypatch.setattr("archon.tooling.worker_tools.list_worker_sessions", lambda limit=10: records)

        result = reg.execute("worker_list", {})
        assert "sess-2" in result
        assert "claude_code" in result

    def test_worker_send_continues_claude_session(self, monkeypatch, tmp_path):
        reg = make_registry()
        captured = {}
        base_record = WorkerSessionRecord(
            session_id="sess-claude",
            created_at="2026-02-24T00:00:00Z",
            updated_at="2026-02-24T00:00:10Z",
            completed_at="2026-02-24T00:00:10Z",
            requested_worker="claude_code",
            selected_worker="claude_code",
            mode="review",
            status="ok",
            repo_path=str(tmp_path),
            task="Initial review",
            constraints="Do not edit files.",
            timeout_sec=900,
            summary="Initial summary",
            exit_code=0,
            error="",
            vendor_session_id="vendor-123",
            event_count=2,
            turn_count=1,
        )
        base_task = WorkerTask(
            task="Initial review",
            worker="claude_code",
            mode="review",
            repo_path=str(tmp_path),
            timeout_sec=900,
            constraints="Do not edit files.",
            model="",
        )

        monkeypatch.setattr("archon.tooling.worker_tools.load_worker_session", lambda sid: base_record if sid == "sess-claude" else None)
        monkeypatch.setattr("archon.tooling.worker_tools.load_worker_task", lambda sid: base_task)
        monkeypatch.setattr("archon.tooling.worker_tools.list_worker_approvals", lambda sid, pending_only=True: [])

        def fake_run(task):
            captured["task"] = task
            return WorkerResult(
                worker="claude_code",
                status="ok",
                summary="Follow-up complete",
                repo_path=str(tmp_path),
                final_message="Follow-up answer",
                vendor_session_id="vendor-123",
                events=[WorkerEvent(kind="result", payload={"type": "result"})],
            )

        monkeypatch.setattr("archon.tooling.worker_tools.run_worker_task", fake_run)
        monkeypatch.setattr(
            "archon.tooling.worker_tools.append_worker_turn",
            lambda sid, task, result: WorkerSessionRecord(
                session_id="sess-claude",
                created_at=base_record.created_at,
                updated_at="2026-02-24T00:01:00Z",
                completed_at="2026-02-24T00:01:00Z",
                requested_worker=base_record.requested_worker,
                selected_worker="claude_code",
                mode="review",
                status="ok",
                repo_path=str(tmp_path),
                task=base_record.task,
                constraints=base_record.constraints,
                timeout_sec=900,
                summary="Follow-up complete",
                exit_code=0,
                error="",
                vendor_session_id="vendor-123",
                event_count=3,
                turn_count=2,
            ),
        )

        result = reg.execute(
            "worker_send",
            {"session_id": "sess-claude", "message": "Please double-check edge cases"},
        )
        assert "archon_session_id: sess-claude" in result
        assert "turn_count: 2" in result
        assert captured["task"].resume_vendor_session_id == "vendor-123"
        assert captured["task"].constraints == "Do not edit files."

    def test_worker_send_continues_opencode_session(self, monkeypatch, tmp_path):
        reg = make_registry()
        captured = {}
        base_record = WorkerSessionRecord(
            session_id="sess-opencode",
            created_at="2026-02-24T00:00:00Z",
            updated_at="2026-02-24T00:00:10Z",
            completed_at="2026-02-24T00:00:10Z",
            requested_worker="opencode",
            selected_worker="opencode",
            mode="review",
            status="ok",
            repo_path=str(tmp_path),
            task="Initial review",
            constraints="Read-only analysis.",
            timeout_sec=900,
            summary="Initial summary",
            exit_code=0,
            error="",
            vendor_session_id="oc-123",
            event_count=2,
            turn_count=1,
        )
        base_task = WorkerTask(
            task="Initial review",
            worker="opencode",
            mode="review",
            repo_path=str(tmp_path),
            timeout_sec=900,
            constraints="Read-only analysis.",
            model="",
        )

        monkeypatch.setattr("archon.tooling.worker_tools.load_worker_session", lambda sid: base_record if sid == "sess-opencode" else None)
        monkeypatch.setattr("archon.tooling.worker_tools.load_worker_task", lambda sid: base_task)
        monkeypatch.setattr("archon.tooling.worker_tools.list_worker_approvals", lambda sid, pending_only=True: [])

        def fake_run(task):
            captured["task"] = task
            return WorkerResult(
                worker="opencode",
                status="ok",
                summary="Follow-up complete",
                repo_path=str(tmp_path),
                final_message="OpenCode follow-up answer",
                vendor_session_id="oc-123",
                events=[WorkerEvent(kind="result", payload={"type": "result"})],
            )

        monkeypatch.setattr("archon.tooling.worker_tools.run_worker_task", fake_run)
        monkeypatch.setattr(
            "archon.tooling.worker_tools.append_worker_turn",
            lambda sid, task, result: WorkerSessionRecord(
                session_id="sess-opencode",
                created_at=base_record.created_at,
                updated_at="2026-02-24T00:01:00Z",
                completed_at="2026-02-24T00:01:00Z",
                requested_worker=base_record.requested_worker,
                selected_worker="opencode",
                mode="review",
                status="ok",
                repo_path=str(tmp_path),
                task=base_record.task,
                constraints=base_record.constraints,
                timeout_sec=900,
                summary="Follow-up complete",
                exit_code=0,
                error="",
                vendor_session_id="oc-123",
                event_count=3,
                turn_count=2,
            ),
        )

        result = reg.execute(
            "worker_send",
            {"session_id": "sess-opencode", "message": "Now focus on deployment details"},
        )
        assert "archon_session_id: sess-opencode" in result
        assert "selected_worker: opencode" in result
        assert "turn_count: 2" in result
        assert captured["task"].resume_vendor_session_id == "oc-123"
        assert captured["task"].constraints == "Read-only analysis."

    def test_worker_poll_uses_cursor(self, monkeypatch):
        reg = make_registry()
        record = WorkerSessionRecord(
            session_id="sess-poll",
            created_at="2026-02-24T00:00:00Z",
            updated_at="2026-02-24T00:00:10Z",
            completed_at="2026-02-24T00:00:10Z",
            requested_worker="auto",
            selected_worker="claude_code",
            mode="review",
            status="ok",
            repo_path="/tmp/repo",
            task="Review",
            constraints="",
            timeout_sec=300,
            summary="Done",
            exit_code=0,
            error="",
            event_count=3,
            turn_count=2,
        )
        events = [
            WorkerEvent(kind="a", payload={"n": 1}),
            WorkerEvent(kind="b", payload={"n": 2}),
            WorkerEvent(kind="c", payload={"n": 3}),
        ]
        monkeypatch.setattr("archon.tooling.worker_tools.load_worker_session", lambda sid: record)
        monkeypatch.setattr("archon.tooling.worker_tools.load_worker_events", lambda sid, limit=0: events)
        monkeypatch.setattr("archon.tooling.worker_tools.list_worker_approvals", lambda sid, pending_only=True: [])

        result = reg.execute("worker_poll", {"session_id": "sess-poll", "cursor": 1, "max_events": 1})
        assert "next_cursor: 2" in result
        assert "returned_events: 1" in result
        assert "- b:" in result

    def test_worker_cancel_marks_session(self, monkeypatch):
        reg = make_registry()
        record = WorkerSessionRecord(
            session_id="sess-cancel",
            created_at="2026-02-24T00:00:00Z",
            updated_at="2026-02-24T00:00:10Z",
            completed_at="2026-02-24T00:00:10Z",
            requested_worker="auto",
            selected_worker="claude_code",
            mode="review",
            status="ok",
            repo_path="/tmp/repo",
            task="Review",
            constraints="",
            timeout_sec=300,
            summary="Done",
            exit_code=0,
            error="",
            event_count=1,
            turn_count=1,
        )
        cancelled = WorkerSessionRecord(
            **{**record.__dict__, "status": "cancelled", "cancelled_at": "2026-02-24T00:02:00Z"}
        )
        monkeypatch.setattr("archon.tooling.worker_tools.load_worker_session", lambda sid: record)
        monkeypatch.setattr("archon.tooling.worker_tools.cancel_worker_session", lambda sid, reason="Cancelled by user": cancelled)

        result = reg.execute("worker_cancel", {"session_id": "sess-cancel"})
        assert "status: cancelled" in result

    def test_worker_approve_updates_pending_request(self, monkeypatch):
        reg = make_registry()
        record = WorkerSessionRecord(
            session_id="sess-approve",
            created_at="2026-02-24T00:00:00Z",
            updated_at="2026-02-24T00:00:10Z",
            completed_at="2026-02-24T00:00:10Z",
            requested_worker="claude_code",
            selected_worker="claude_code",
            mode="review",
            status="waiting_approval",
            repo_path="/tmp/repo",
            task="Review",
            constraints="",
            timeout_sec=300,
            summary="Waiting",
            exit_code=0,
            error="",
            event_count=1,
            turn_count=1,
        )
        approval = type("A", (), {
            "request_id": "req-1",
            "status": "approved",
            "action": "Edit(file.py)",
            "details": "Allow edit of file.py",
            "note": "ok",
        })()
        monkeypatch.setattr("archon.tooling.worker_tools.load_worker_session", lambda sid: record)
        monkeypatch.setattr("archon.tooling.worker_tools.decide_worker_approval", lambda sid, request_id, decision, note="": approval)

        result = reg.execute(
            "worker_approve",
            {"session_id": "sess-approve", "request_id": "req-1", "decision": "approve", "note": "ok"},
        )
        assert "request_id: req-1" in result
        assert "status: approved" in result

    def test_worker_reconcile_repairs_orphaned_session(self, monkeypatch):
        reg = make_registry()
        record = WorkerSessionRecord(
            session_id="sess-orphan",
            created_at="2026-02-24T00:00:00Z",
            updated_at="2026-02-24T00:05:00Z",
            completed_at="",
            requested_worker="opencode",
            selected_worker="opencode",
            mode="review",
            status="running",
            repo_path="/tmp/repo",
            task="Deep analysis",
            constraints="",
            timeout_sec=900,
            summary="Worker session reserved",
            exit_code=None,
            error="",
            event_count=10,
            turn_count=1,
        )
        reconciled = WorkerSessionRecord(
            **{
                **record.__dict__,
                "status": "error",
                "completed_at": "2026-02-24T00:06:00Z",
                "updated_at": "2026-02-24T00:06:00Z",
                "summary": "Reconciled orphaned worker session",
                "error": "Reconciled orphaned worker session",
            }
        )
        monkeypatch.setattr("archon.tooling.worker_tools.load_worker_session", lambda sid: record if sid == "sess-orphan" else None)
        monkeypatch.setattr("archon.tooling.worker_tools.get_background_run", lambda sid: None)
        monkeypatch.setattr("archon.tooling.worker_tools.reconcile_worker_session", lambda *args, **kwargs: reconciled)

        result = reg.execute("worker_reconcile", {"session_id": "sess-orphan"})
        assert "archon_session_id: sess-orphan" in result
        assert "status: error" in result

    def test_worker_reconcile_refuses_active_runtime_without_force(self, monkeypatch):
        reg = make_registry()
        record = WorkerSessionRecord(
            session_id="sess-live",
            created_at="2026-02-24T00:00:00Z",
            updated_at="2026-02-24T00:05:00Z",
            completed_at="",
            requested_worker="opencode",
            selected_worker="opencode",
            mode="review",
            status="running",
            repo_path="/tmp/repo",
            task="Deep analysis",
            constraints="",
            timeout_sec=900,
            summary="Running",
            exit_code=None,
            error="",
            event_count=12,
            turn_count=1,
        )
        monkeypatch.setattr("archon.tooling.worker_tools.load_worker_session", lambda sid: record if sid == "sess-live" else None)
        monkeypatch.setattr(
            "archon.tooling.worker_tools.get_background_run",
            lambda sid: ActiveWorkerRun(
                session_id="sess-live",
                requested_worker="opencode",
                state="running",
                started_at="2026-02-24T00:00:00Z",
                updated_at="2026-02-24T00:05:01Z",
                thread_name="archon-worker-sesslive",
                pid=12345,
                process_state="running",
            ),
        )

        result = reg.execute("worker_reconcile", {"session_id": "sess-live"})
        assert "reconcile: refused" in result
        assert "runtime_state: running" in result

    def test_worker_send_blocks_when_pending_approvals_exist(self, monkeypatch, tmp_path):
        reg = make_registry()
        record = WorkerSessionRecord(
            session_id="sess-pending",
            created_at="2026-02-24T00:00:00Z",
            updated_at="2026-02-24T00:00:10Z",
            completed_at="2026-02-24T00:00:10Z",
            requested_worker="claude_code",
            selected_worker="claude_code",
            mode="review",
            status="waiting_approval",
            repo_path=str(tmp_path),
            task="Review",
            constraints="",
            timeout_sec=300,
            summary="Waiting",
            exit_code=0,
            error="",
            vendor_session_id="vendor-1",
            event_count=1,
            turn_count=1,
        )
        monkeypatch.setattr("archon.tooling.worker_tools.load_worker_session", lambda sid: record)
        monkeypatch.setattr("archon.tooling.worker_tools.list_worker_approvals", lambda sid, pending_only=True: [object()])

        result = reg.execute("worker_send", {"session_id": "sess-pending", "message": "continue"})
        assert "pending approval request" in result.lower()

    def test_worker_start_background_uses_runtime(self, monkeypatch, tmp_path):
        reg = ToolRegistry(archon_source_dir=None, confirmer=lambda _c, _l: True)

        monkeypatch.setattr(
            "archon.tooling.worker_tools.start_background_worker",
            lambda task, requested_worker: ActiveWorkerRun(
                session_id="sess-bg",
                requested_worker=requested_worker,
                state="starting",
                started_at="2026-02-24T00:00:00Z",
                updated_at="2026-02-24T00:00:00Z",
                thread_name="archon-worker-sessbg",
            ),
        )

        result = reg.execute(
            "worker_start",
            {
                "task": "Implement feature",
                "worker": "claude_code",
                "mode": "implement",
                "repo_path": str(tmp_path),
                "background": True,
            },
        )
        assert "archon_session_id: sess-bg" in result
        assert "background: started" in result

    def test_worker_status_includes_runtime_state(self, monkeypatch):
        reg = make_registry()
        record = WorkerSessionRecord(
            session_id="sess-live",
            created_at="2026-02-24T00:00:00Z",
            updated_at="2026-02-24T00:00:10Z",
            completed_at="",
            requested_worker="claude_code",
            selected_worker="claude_code",
            mode="implement",
            status="running",
            repo_path="/tmp/repo",
            task="Implement",
            constraints="",
            timeout_sec=300,
            summary="Running",
            exit_code=None,
            error="",
            event_count=0,
            turn_count=0,
        )
        monkeypatch.setattr("archon.tooling.worker_tools.load_worker_session", lambda sid: record)
        monkeypatch.setattr("archon.tooling.worker_tools.list_worker_approvals", lambda sid, pending_only=True: [])
        monkeypatch.setattr("archon.tooling.worker_tools.load_worker_result", lambda sid: None)
        monkeypatch.setattr("archon.tooling.worker_tools.load_worker_events", lambda sid, limit=25: [])
        monkeypatch.setattr(
            "archon.tooling.worker_tools.get_background_run",
            lambda sid: ActiveWorkerRun(
                session_id="sess-live",
                requested_worker="claude_code",
                state="running",
                started_at="2026-02-24T00:00:00Z",
                updated_at="2026-02-24T00:00:11Z",
                thread_name="archon-worker-live",
            ),
        )

        result = reg.execute("worker_status", {"session_id": "sess-live"})
        assert "runtime_state: running" in result
        assert "runtime_thread: archon-worker-live" in result
