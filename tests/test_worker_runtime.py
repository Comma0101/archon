"""Tests for background worker runtime."""

import time
import threading

from archon.workers.base import WorkerEvent, WorkerResult, WorkerTask
from archon.workers.runtime import (
    get_background_run,
    request_background_cancel,
    start_background_worker,
)


def _wait_for_worker_threads(session_id: str, timeout: float = 1.0) -> None:
    prefixes = {
        f"archon-worker-{session_id[:8]}",
        f"archon-worker-kill-{session_id[:8]}",
    }
    deadline = time.time() + timeout
    while time.time() < deadline:
        live = {
            thread.name
            for thread in threading.enumerate()
            if thread.name in prefixes
        }
        if not live:
            return
        time.sleep(0.01)
    raise AssertionError(f"background worker threads still alive for {session_id}")


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
        _wait_for_worker_threads("sess-bg-1")

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
        _wait_for_worker_threads("sess-bg-2")

    def test_background_worker_preserves_terminal_failure_state(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "archon.workers.runtime.reserve_worker_session",
            lambda task, requested_worker: type("R", (), {"session_id": "sess-bg-3"})(),
        )
        monkeypatch.setattr(
            "archon.workers.runtime.append_worker_events",
            lambda session_id, events: None,
        )
        monkeypatch.setattr(
            "archon.workers.runtime.run_worker_task",
            lambda task, exec_observer=None: WorkerResult(
                worker="codex",
                status="failed",
                summary="worker failed",
                repo_path=task.repo_path,
                error="boom",
            ),
        )
        monkeypatch.setattr(
            "archon.workers.runtime.record_worker_run",
            lambda task, result, requested_worker: type("Rec", (), {"session_id": "sess-bg-3"})(),
        )

        start_background_worker(
            WorkerTask(task="Review", worker="codex", mode="review", repo_path=str(tmp_path)),
            requested_worker="codex",
        )

        for _ in range(50):
            run = get_background_run("sess-bg-3")
            if run and run.state == "failed":
                break
            time.sleep(0.01)

        run = get_background_run("sess-bg-3")
        assert run is not None
        assert run.state == "failed"
        _wait_for_worker_threads("sess-bg-3")
