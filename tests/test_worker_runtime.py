"""Tests for background worker runtime."""

import time
import threading

from archon.workers.base import WorkerEvent, WorkerResult, WorkerTask
from archon.workers.runtime import (
    get_background_run,
    request_background_cancel,
    start_background_worker,
)


class TestWorkerRuntime:
    def test_background_worker_records_completion(self, monkeypatch, tmp_path):
        calls = {"run": 0, "record": 0}
        streamed_events = []

        monkeypatch.setattr(
            "archon.workers.runtime.reserve_worker_session",
            lambda task, requested_worker: type("R", (), {"session_id": "sess-bg-1"})(),
        )
        monkeypatch.setattr(
            "archon.workers.runtime.append_worker_events",
            lambda session_id, events: streamed_events.extend((session_id, e.kind) for e in events),
        )

        class FakeProc:
            pid = 4242

            def __init__(self):
                self._rc = None

            def poll(self):
                return self._rc

            def terminate(self):
                self._rc = -15

            def kill(self):
                self._rc = -9

            def wait(self, timeout=None):
                if self._rc is None:
                    self._rc = 0
                return self._rc

        def fake_run_worker_task(task, exec_observer=None):
            calls["run"] += 1
            assert task.archon_session_id == "sess-bg-1"
            proc = FakeProc()
            if exec_observer is not None:
                exec_observer.on_process_started(proc)
                exec_observer.on_process_output("stdout", "hello from worker\n")
                exec_observer.on_process_output("stderr", "warn from worker\n")
                proc._rc = 0
                exec_observer.on_process_exit(0)
            return WorkerResult(worker="codex", status="ok", summary="done", repo_path=task.repo_path)

        def fake_record_worker_run(task, result, requested_worker):
            calls["record"] += 1
            assert task.archon_session_id == "sess-bg-1"
            return type("Rec", (), {"session_id": "sess-bg-1"})()

        monkeypatch.setattr("archon.workers.runtime.run_worker_task", fake_run_worker_task)
        monkeypatch.setattr("archon.workers.runtime.record_worker_run", fake_record_worker_run)

        active = start_background_worker(
            WorkerTask(task="Review", worker="codex", mode="review", repo_path=str(tmp_path)),
            requested_worker="codex",
        )
        assert active.session_id == "sess-bg-1"

        # Wait briefly for background thread to finish
        for _ in range(50):
            run = get_background_run("sess-bg-1")
            if run and run.state == "completed":
                break
            time.sleep(0.01)

        run = get_background_run("sess-bg-1")
        assert run is not None
        assert run.state == "completed"
        assert run.pid == 4242
        assert run.process_state == "exited"
        assert run.process_returncode == 0
        assert calls["run"] == 1
        assert calls["record"] == 1
        assert ("sess-bg-1", "runtime.process.started") in streamed_events
        assert ("sess-bg-1", "runtime.stdout.line") in streamed_events
        assert ("sess-bg-1", "runtime.stderr.line") in streamed_events
        assert ("sess-bg-1", "runtime.process.exited") in streamed_events

    def test_request_background_cancel_sets_flag(self, monkeypatch, tmp_path):
        terminate_called = {"value": False}

        monkeypatch.setattr(
            "archon.workers.runtime.reserve_worker_session",
            lambda task, requested_worker: type("R", (), {"session_id": "sess-bg-2"})(),
        )
        monkeypatch.setattr(
            "archon.workers.runtime.append_worker_events",
            lambda session_id, events: None,
        )

        class FakeProc:
            pid = 5252

            def __init__(self):
                self._rc = None
                self._term_event = threading.Event()

            def poll(self):
                return self._rc

            def terminate(self):
                terminate_called["value"] = True
                self._rc = -15
                self._term_event.set()

            def kill(self):
                self._rc = -9
                self._term_event.set()

            def wait(self, timeout=None):
                self._term_event.wait(timeout or 0)
                return self._rc

        def fake_run_worker_task(task, exec_observer=None):
            proc = FakeProc()
            if exec_observer is not None:
                exec_observer.on_process_started(proc)
            deadline = time.time() + 0.5
            while time.time() < deadline:
                if exec_observer is not None and exec_observer.is_cancel_requested():
                    break
                time.sleep(0.01)
            if exec_observer is not None:
                exec_observer.on_process_exit(proc.poll())
            return WorkerResult(worker="codex", status="ok", summary="done", repo_path=task.repo_path)

        monkeypatch.setattr("archon.workers.runtime.run_worker_task", fake_run_worker_task)
        monkeypatch.setattr(
            "archon.workers.runtime.record_worker_run",
            lambda task, result, requested_worker: type("Rec", (), {"session_id": "sess-bg-2"})(),
        )

        start_background_worker(
            WorkerTask(task="Review", worker="codex", mode="review", repo_path=str(tmp_path)),
            requested_worker="codex",
        )
        assert request_background_cancel("sess-bg-2") is True
        run = get_background_run("sess-bg-2")
        assert run is not None
        assert run.cancel_requested is True
        assert run.pid == 5252

        for _ in range(50):
            if terminate_called["value"]:
                break
            time.sleep(0.01)
        assert terminate_called["value"] is True
