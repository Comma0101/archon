"""Tests for worker session persistence."""

import json
from pathlib import Path

from archon.control.hooks import HookBus
import archon.workers.session_store as session_store
from archon.workers.base import WorkerEvent, WorkerResult, WorkerTask
from archon.workers.session_store import (
    WORKER_EVENTS_DIR,
    WORKER_SESSIONS_DIR,
    add_worker_approval_request,
    append_worker_turn,
    cancel_worker_session,
    decide_worker_approval,
    format_worker_session_list,
    list_worker_job_summaries,
    list_worker_approvals,
    load_worker_events,
    load_worker_job_summary,
    load_worker_result,
    load_worker_session,
    load_worker_task,
    reconcile_worker_session,
    record_worker_run,
)


class TestWorkerSessionStore:
    def test_record_worker_run_emits_completion_via_explicit_hook_bus(self, monkeypatch, tmp_path):
        sessions_dir = tmp_path / "workers" / "sessions"
        events_dir = tmp_path / "workers" / "events"
        monkeypatch.setattr("archon.workers.session_store.WORKER_SESSIONS_DIR", sessions_dir)
        monkeypatch.setattr("archon.workers.session_store.WORKER_EVENTS_DIR", events_dir)
        monkeypatch.delattr(session_store._emit_job_completed_event, "_hook_bus", raising=False)

        hook_bus = HookBus()
        seen = []
        hook_bus.register("ux.job_completed", lambda event: seen.append(event.payload["event"]))

        task = WorkerTask(
            task="Review repo",
            worker="auto",
            mode="review",
            repo_path=str(tmp_path),
            timeout_sec=60,
            constraints="Read-only",
        )
        result = WorkerResult(
            worker="codex",
            status="ok",
            summary="Looks good",
            repo_path=str(tmp_path),
            exit_code=0,
        )

        record = record_worker_run(task, result, requested_worker="auto", hook_bus=hook_bus)

        assert record.status == "ok"
        assert len(seen) == 1
        assert seen[0].kind == "job_completed"
        assert seen[0].data["job_id"] == record.session_id
        assert seen[0].data["status"] == "ok"
        assert not hasattr(session_store._emit_job_completed_event, "_hook_bus")

    def test_record_and_load_roundtrip(self, monkeypatch, tmp_path):
        sessions_dir = tmp_path / "workers" / "sessions"
        events_dir = tmp_path / "workers" / "events"
        monkeypatch.setattr("archon.workers.session_store.WORKER_SESSIONS_DIR", sessions_dir)
        monkeypatch.setattr("archon.workers.session_store.WORKER_EVENTS_DIR", events_dir)

        task = WorkerTask(
            task="Review repo",
            worker="auto",
            mode="review",
            repo_path=str(tmp_path),
            timeout_sec=60,
            constraints="Read-only",
        )
        result = WorkerResult(
            worker="codex",
            status="ok",
            summary="Looks good",
            repo_path=str(tmp_path),
            exit_code=0,
            final_message="No critical issues.",
            events=[WorkerEvent(kind="session.started", payload={"type": "session.started"})],
            vendor_session_id="vendor-1",
        )

        record = record_worker_run(task, result, requested_worker="auto")

        assert (sessions_dir / f"{record.session_id}.json").exists()
        assert (events_dir / f"{record.session_id}.jsonl").exists()

        loaded_record = load_worker_session(record.session_id)
        assert loaded_record is not None
        assert loaded_record.selected_worker == "codex"
        assert loaded_record.vendor_session_id == "vendor-1"

        loaded_result = load_worker_result(record.session_id)
        assert loaded_result is not None
        assert loaded_result.final_message == "No critical issues."

        loaded_events = load_worker_events(record.session_id)
        assert len(loaded_events) == 1
        assert loaded_events[0].kind == "session.started"

    def test_record_and_append_queue_worker_memory_candidates(self, monkeypatch, tmp_path):
        sessions_dir = tmp_path / "workers" / "sessions"
        events_dir = tmp_path / "workers" / "events"
        monkeypatch.setattr("archon.workers.session_store.WORKER_SESSIONS_DIR", sessions_dir)
        monkeypatch.setattr("archon.workers.session_store.WORKER_EVENTS_DIR", events_dir)

        queued = []
        monkeypatch.setattr(
            "archon.workers.session_store._maybe_queue_worker_summary_candidate",
            lambda record, task, result: queued.append((record.session_id, task.mode, result.summary)),
        )

        task1 = WorkerTask(task="Review repo", worker="opencode", mode="review", repo_path=str(tmp_path))
        result1 = WorkerResult(worker="opencode", status="ok", summary="Deep review found missing README and hardcoded data.", repo_path=str(tmp_path))
        rec1 = record_worker_run(task1, result1, requested_worker="opencode")
        assert rec1 is not None

        task2 = WorkerTask(task="Implement README", worker="opencode", mode="implement", repo_path=str(tmp_path), resume_vendor_session_id="vendor-1")
        result2 = WorkerResult(worker="opencode", status="ok", summary="Created README draft with setup and architecture overview.", repo_path=str(tmp_path))
        rec2 = append_worker_turn(rec1.session_id, task2, result2)
        assert rec2 is not None

        assert len(queued) == 2
        assert queued[0][1] == "review"
        assert queued[1][1] == "implement"

    def test_format_session_list_empty(self, monkeypatch, tmp_path):
        monkeypatch.setattr("archon.workers.session_store.WORKER_SESSIONS_DIR", tmp_path / "missing")
        text = format_worker_session_list([])
        assert "No worker sessions" in text

    def test_append_turn_and_cancel(self, monkeypatch, tmp_path):
        sessions_dir = tmp_path / "workers" / "sessions"
        events_dir = tmp_path / "workers" / "events"
        monkeypatch.setattr("archon.workers.session_store.WORKER_SESSIONS_DIR", sessions_dir)
        monkeypatch.setattr("archon.workers.session_store.WORKER_EVENTS_DIR", events_dir)

        task1 = WorkerTask(task="Review repo", worker="claude_code", mode="review", repo_path=str(tmp_path))
        result1 = WorkerResult(
            worker="claude_code",
            status="ok",
            summary="Turn 1",
            repo_path=str(tmp_path),
            events=[WorkerEvent(kind="session.started", payload={"n": 1})],
            vendor_session_id="vendor-1",
        )
        rec1 = record_worker_run(task1, result1, requested_worker="claude_code")

        task2 = WorkerTask(
            task="Follow up",
            worker="claude_code",
            mode="review",
            repo_path=str(tmp_path),
            resume_vendor_session_id="vendor-1",
        )
        result2 = WorkerResult(
            worker="claude_code",
            status="ok",
            summary="Turn 2",
            repo_path=str(tmp_path),
            events=[WorkerEvent(kind="result", payload={"n": 2})],
            vendor_session_id="vendor-1",
        )
        rec2 = append_worker_turn(rec1.session_id, task2, result2)
        assert rec2 is not None
        assert rec2.turn_count == 2
        assert rec2.event_count == 2

        loaded_task = load_worker_task(rec1.session_id)
        assert loaded_task is not None
        assert loaded_task.task == "Follow up"
        assert loaded_task.resume_vendor_session_id == "vendor-1"

        events = load_worker_events(rec1.session_id, limit=0)
        assert len(events) == 2
        assert events[-1].kind == "result"

        cancelled = cancel_worker_session(rec1.session_id, reason="user stop")
        assert cancelled is not None
        assert cancelled.status == "cancelled"
        assert cancelled.turn_count == 2
        assert cancelled.cancelled_at

    def test_append_turn_updates_record_mode(self, monkeypatch, tmp_path):
        sessions_dir = tmp_path / "workers" / "sessions"
        events_dir = tmp_path / "workers" / "events"
        monkeypatch.setattr("archon.workers.session_store.WORKER_SESSIONS_DIR", sessions_dir)
        monkeypatch.setattr("archon.workers.session_store.WORKER_EVENTS_DIR", events_dir)

        rec1 = record_worker_run(
            WorkerTask(task="Review repo", worker="claude_code", mode="review", repo_path=str(tmp_path)),
            WorkerResult(worker="claude_code", status="ok", summary="Turn 1", repo_path=str(tmp_path)),
            requested_worker="claude_code",
        )

        rec2 = append_worker_turn(
            rec1.session_id,
            WorkerTask(
                task="Implement fix",
                worker="claude_code",
                mode="implement",
                repo_path=str(tmp_path),
                resume_vendor_session_id="vendor-1",
            ),
            WorkerResult(worker="claude_code", status="ok", summary="Turn 2", repo_path=str(tmp_path)),
        )
        assert rec2 is not None
        assert rec2.mode == "implement"

        loaded = load_worker_session(rec1.session_id)
        assert loaded is not None
        assert loaded.mode == "implement"

    def test_approval_request_roundtrip(self, monkeypatch, tmp_path):
        sessions_dir = tmp_path / "workers" / "sessions"
        events_dir = tmp_path / "workers" / "events"
        monkeypatch.setattr("archon.workers.session_store.WORKER_SESSIONS_DIR", sessions_dir)
        monkeypatch.setattr("archon.workers.session_store.WORKER_EVENTS_DIR", events_dir)

        task = WorkerTask(task="Implement fix", worker="claude_code", mode="implement", repo_path=str(tmp_path))
        result = WorkerResult(worker="claude_code", status="paused", summary="waiting", repo_path=str(tmp_path))
        rec = record_worker_run(task, result, requested_worker="claude_code")

        req = add_worker_approval_request(rec.session_id, action="Edit", details="Edit src/app.py")
        assert req is not None
        assert req.status == "pending"

        pending = list_worker_approvals(rec.session_id, pending_only=True)
        assert len(pending) == 1
        assert pending[0].request_id == req.request_id

        decided = decide_worker_approval(rec.session_id, req.request_id, "approve", note="ok")
        assert decided is not None
        assert decided.status == "approved"
        assert decided.note == "ok"

        pending_after = list_worker_approvals(rec.session_id, pending_only=True)
        assert pending_after == []

    def test_reconcile_orphaned_running_session(self, monkeypatch, tmp_path):
        sessions_dir = tmp_path / "workers" / "sessions"
        events_dir = tmp_path / "workers" / "events"
        monkeypatch.setattr("archon.workers.session_store.WORKER_SESSIONS_DIR", sessions_dir)
        monkeypatch.setattr("archon.workers.session_store.WORKER_EVENTS_DIR", events_dir)

        task = WorkerTask(task="Deep analysis", worker="opencode", mode="review", repo_path=str(tmp_path))
        rec = record_worker_run(
            task,
            WorkerResult(worker="opencode", status="ok", summary="done", repo_path=str(tmp_path)),
            requested_worker="opencode",
        )

        payload_path = sessions_dir / f"{rec.session_id}.json"
        payload = json.loads(payload_path.read_text())
        payload["record"]["status"] = "running"
        payload["record"]["completed_at"] = ""
        payload["record"]["summary"] = "Worker session reserved"
        payload["record"]["error"] = ""
        payload_path.write_text(json.dumps(payload))

        repaired = reconcile_worker_session(rec.session_id)
        assert repaired is not None
        assert repaired.status == "error"
        assert repaired.completed_at
        assert repaired.error

        events = load_worker_events(rec.session_id, limit=0)
        assert any(e.kind == "session.reconciled" for e in events)

    def test_load_worker_job_summary_auto_reconciles_stale_reserved_session(self, monkeypatch, tmp_path):
        sessions_dir = tmp_path / "workers" / "sessions"
        events_dir = tmp_path / "workers" / "events"
        monkeypatch.setattr("archon.workers.session_store.WORKER_SESSIONS_DIR", sessions_dir)
        monkeypatch.setattr("archon.workers.session_store.WORKER_EVENTS_DIR", events_dir)
        monkeypatch.setattr("archon.workers.runtime.get_background_run", lambda session_id: None)

        task = WorkerTask(task="Deep analysis", worker="opencode", mode="review", repo_path=str(tmp_path))
        reserved = session_store.reserve_worker_session(task, requested_worker="opencode")

        job = load_worker_job_summary(reserved.session_id)

        assert job is not None
        assert job.job_id == f"worker:{reserved.session_id}"
        assert job.status == "error"
        assert job.summary == "Worker session never started"

        record = load_worker_session(reserved.session_id)
        assert record is not None
        assert record.status == "error"
        assert record.summary == "Worker session never started"
        assert record.completed_at

    def test_list_worker_job_summaries_preserves_live_reserved_runtime(self, monkeypatch, tmp_path):
        sessions_dir = tmp_path / "workers" / "sessions"
        events_dir = tmp_path / "workers" / "events"
        monkeypatch.setattr("archon.workers.session_store.WORKER_SESSIONS_DIR", sessions_dir)
        monkeypatch.setattr("archon.workers.session_store.WORKER_EVENTS_DIR", events_dir)

        task = WorkerTask(task="Deep analysis", worker="opencode", mode="review", repo_path=str(tmp_path))
        reserved = session_store.reserve_worker_session(task, requested_worker="opencode")
        active_run = type("ActiveRun", (), {"state": "running"})()
        monkeypatch.setattr(
            "archon.workers.runtime.get_background_run",
            lambda session_id: active_run if session_id == reserved.session_id else None,
        )

        jobs = list_worker_job_summaries(limit=10)

        assert len(jobs) == 1
        assert jobs[0].job_id == f"worker:{reserved.session_id}"
        assert jobs[0].status == "running"
        assert jobs[0].summary == "Worker session reserved"

    def test_worker_summary_candidate_uses_unique_project_lookup_hit(self, monkeypatch, tmp_path):
        captured = {}

        def fake_lookup(query, limit=10):
            assert "korami-site" in query
            return [
                {
                    "path": "projects/korami-site.md",
                    "kind": "project",
                    "scope": "project:korami-site",
                    "score": 18.0,
                }
            ]

        def fake_inbox_add(**kwargs):
            captured.update(kwargs)
            return {"id": "mem-1"}

        monkeypatch.setattr("archon.memory.lookup", fake_lookup)
        monkeypatch.setattr("archon.memory.inbox_add", fake_inbox_add)

        record = session_store.WorkerSessionRecord(
            session_id="sess-1",
            created_at="2026-02-24T00:00:00Z",
            updated_at="2026-02-24T00:00:00Z",
            completed_at="2026-02-24T00:00:01Z",
            requested_worker="opencode",
            selected_worker="opencode",
            mode="review",
            status="ok",
            repo_path=str(tmp_path / "korami-site"),
            task="Deep review",
            constraints="",
            timeout_sec=0,
            summary="done",
            exit_code=0,
            error="",
        )
        task = WorkerTask(task="Deep review", worker="opencode", mode="review", repo_path=str(tmp_path / "korami-site"))
        result = WorkerResult(worker="opencode", status="ok", summary="Solid architecture and missing README.", repo_path=str(tmp_path / "korami-site"))

        session_store._maybe_queue_worker_summary_candidate(record, task, result)

        assert captured["target_path"] == "projects/korami-site.md"
        assert captured["scope"] == "project:korami-site"
        assert captured["kind"] == "worker_summary"

    def test_worker_summary_candidate_skips_ambiguous_project_lookup(self, monkeypatch, tmp_path):
        called = {"inbox_add": 0}

        monkeypatch.setattr(
            "archon.memory.lookup",
            lambda _query, limit=10: [
                {"path": "projects/alpha-backend.md", "kind": "project", "scope": "project:alpha-backend", "score": 12.0},
                {"path": "projects/beta-backend.md", "kind": "project", "scope": "project:beta-backend", "score": 11.8},
            ],
        )
        monkeypatch.setattr(
            "archon.memory.inbox_add",
            lambda **_kwargs: called.__setitem__("inbox_add", called["inbox_add"] + 1),
        )

        record = session_store.WorkerSessionRecord(
            session_id="sess-2",
            created_at="2026-02-24T00:00:00Z",
            updated_at="2026-02-24T00:00:00Z",
            completed_at="2026-02-24T00:00:01Z",
            requested_worker="opencode",
            selected_worker="opencode",
            mode="review",
            status="ok",
            repo_path=str(tmp_path / "backend"),
            task="Deep review",
            constraints="",
            timeout_sec=0,
            summary="done",
            exit_code=0,
            error="",
        )
        task = WorkerTask(task="Deep review", worker="opencode", mode="review", repo_path=str(tmp_path / "backend"))
        result = WorkerResult(worker="opencode", status="ok", summary="Found issues.", repo_path=str(tmp_path / "backend"))

        session_store._maybe_queue_worker_summary_candidate(record, task, result)

        assert called["inbox_add"] == 0
