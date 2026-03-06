"""Streaming/cancellable subprocess helper for worker adapters."""

from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Mapping

from archon.workers.base import WorkerExecObserver


@dataclass
class StreamingProcessResult:
    args: list[str]
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    cancelled: bool = False


def run_streaming_process(
    command: list[str],
    *,
    cwd: str,
    timeout: int,
    env: Mapping[str, str] | None = None,
    exec_observer: WorkerExecObserver | None = None,
    kill_grace_sec: float = 1.0,
) -> StreamingProcessResult:
    """Run a subprocess with line-streaming callbacks and cooperative cancellation."""
    proc = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=dict(env) if env is not None else None,
        bufsize=1,
    )
    if exec_observer is not None:
        try:
            exec_observer.on_process_started(proc)
        except Exception:
            pass

    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    stdout_thread = threading.Thread(
        target=_drain_stream,
        args=(proc.stdout, "stdout", stdout_parts, exec_observer),
        daemon=True,
        name=f"archon-worker-stdout-{proc.pid}",
    )
    stderr_thread = threading.Thread(
        target=_drain_stream,
        args=(proc.stderr, "stderr", stderr_parts, exec_observer),
        daemon=True,
        name=f"archon-worker-stderr-{proc.pid}",
    )
    stdout_thread.start()
    stderr_thread.start()

    deadline = time.monotonic() + max(1, int(timeout))
    terminate_sent_at: float | None = None
    timed_out = False
    cancelled = False
    killed = False

    while True:
        rc = proc.poll()
        if rc is not None:
            break

        now = time.monotonic()
        if terminate_sent_at is None and exec_observer is not None and _observer_cancel_requested(exec_observer):
            cancelled = True
            terminate_sent_at = now
            _observer_signal(exec_observer, "terminate", "cancel")
            _terminate_best_effort(proc)
        elif terminate_sent_at is None and now >= deadline:
            timed_out = True
            terminate_sent_at = now
            _observer_signal(exec_observer, "terminate", "timeout")
            _terminate_best_effort(proc)

        if terminate_sent_at is not None and not killed:
            if proc.poll() is None and (now - terminate_sent_at) >= max(0.1, float(kill_grace_sec)):
                killed = True
                _observer_signal(exec_observer, "kill", "cancel" if cancelled else "timeout")
                _kill_best_effort(proc)

        time.sleep(0.05)

    try:
        proc.wait(timeout=1)
    except Exception:
        pass

    stdout_thread.join(timeout=1)
    stderr_thread.join(timeout=1)

    stdout = "".join(stdout_parts)
    stderr = "".join(stderr_parts)
    if exec_observer is not None:
        try:
            exec_observer.on_process_exit(proc.returncode)
        except Exception:
            pass

    return StreamingProcessResult(
        args=list(command),
        returncode=proc.returncode,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        cancelled=cancelled,
    )


def _drain_stream(pipe, stream_name: str, sink: list[str], exec_observer: WorkerExecObserver | None):
    if pipe is None:
        return
    try:
        for chunk in iter(pipe.readline, ""):
            if chunk == "":
                break
            sink.append(chunk)
            if exec_observer is not None:
                try:
                    exec_observer.on_process_output(stream_name, chunk)
                except Exception:
                    pass
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def _observer_cancel_requested(exec_observer: WorkerExecObserver) -> bool:
    try:
        return bool(exec_observer.is_cancel_requested())
    except Exception:
        return False


def _observer_signal(exec_observer: WorkerExecObserver | None, signal_name: str, reason: str):
    if exec_observer is None:
        return
    try:
        exec_observer.on_process_signal(signal_name, reason=reason)
    except Exception:
        pass


def _terminate_best_effort(proc: subprocess.Popen):
    try:
        proc.terminate()
    except Exception:
        pass


def _kill_best_effort(proc: subprocess.Popen):
    try:
        proc.kill()
    except Exception:
        pass
