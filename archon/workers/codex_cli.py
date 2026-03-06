"""Codex CLI adapter for delegated coding tasks."""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from archon.workers.base import WorkerEvent, WorkerExecObserver, WorkerResult, WorkerTask
from archon.workers.common import (
    first_nonempty_line,
    summarize_cli_run,
)
from archon.workers.subprocess_exec import run_streaming_process


def codex_available() -> bool:
    return shutil.which("codex") is not None


def run_codex_task(task: WorkerTask, exec_observer: WorkerExecObserver | None = None) -> WorkerResult:
    codex_bin = shutil.which("codex")
    workdir = str(Path(task.repo_path).expanduser().resolve())
    if not codex_bin:
        return WorkerResult(
            worker="codex",
            status="unavailable",
            summary="Codex CLI not found on PATH",
            repo_path=workdir,
            error="Install Codex CLI and ensure `codex` is available on PATH.",
        )
    if not Path(workdir).exists():
        return WorkerResult(
            worker="codex",
            status="error",
            summary="Repository path does not exist",
            repo_path=workdir,
            error=f"Not found: {workdir}",
        )
    if not Path(workdir).is_dir():
        return WorkerResult(
            worker="codex",
            status="error",
            summary="Repository path is not a directory",
            repo_path=workdir,
            error=f"Not a directory: {workdir}",
        )

    tmp_output = tempfile.NamedTemporaryFile(
        prefix="archon-codex-",
        suffix=".txt",
        delete=False,
    )
    tmp_output.close()

    command = _build_codex_command(codex_bin, task, tmp_output.name)
    timeout = max(1, int(task.timeout_sec))

    try:
        if exec_observer is None:
            completed = subprocess.run(
                command,
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            completed_stdout = completed.stdout
            completed_stderr = completed.stderr
            completed_returncode = completed.returncode
            timed_out = False
            cancelled = False
        else:
            completed_stream = run_streaming_process(
                command,
                cwd=workdir,
                timeout=timeout,
                exec_observer=exec_observer,
            )
            completed_stdout = completed_stream.stdout
            completed_stderr = completed_stream.stderr
            completed_returncode = completed_stream.returncode or 0
            timed_out = completed_stream.timed_out
            cancelled = completed_stream.cancelled
    except subprocess.TimeoutExpired as e:
        _safe_unlink(tmp_output.name)
        return WorkerResult(
            worker="codex",
            status="timeout",
            summary=f"Timed out after {timeout}s",
            repo_path=workdir,
            command=command,
            stdout=(e.stdout or "") if isinstance(e.stdout, str) else "",
            stderr=(e.stderr or "") if isinstance(e.stderr, str) else "",
            error=f"Timeout after {timeout}s",
        )
    except Exception as e:
        _safe_unlink(tmp_output.name)
        return WorkerResult(
            worker="codex",
            status="error",
            summary=f"Failed to run Codex CLI: {type(e).__name__}",
            repo_path=workdir,
            command=command,
            error=str(e),
        )

    if exec_observer is not None and timed_out:
        _safe_unlink(tmp_output.name)
        return WorkerResult(
            worker="codex",
            status="timeout",
            summary=f"Timed out after {timeout}s",
            repo_path=workdir,
            command=command,
            stdout=completed_stdout,
            stderr=completed_stderr,
            error=f"Timeout after {timeout}s",
        )
    if exec_observer is not None and cancelled:
        _safe_unlink(tmp_output.name)
        return WorkerResult(
            worker="codex",
            status="cancelled",
            summary="Delegated Codex task cancelled",
            repo_path=workdir,
            command=command,
            exit_code=completed_returncode,
            stdout=completed_stdout,
            stderr=completed_stderr,
            error="Cancelled by Archon runtime",
        )

    final_message = _read_text_if_exists(tmp_output.name).strip()
    _safe_unlink(tmp_output.name)

    events = _parse_jsonl_events(completed_stdout)
    status = "ok" if completed_returncode == 0 else "failed"

    return WorkerResult(
        worker="codex",
        status=status,
        summary=summarize_cli_run("Codex", status, completed_returncode, final_message, completed_stderr),
        repo_path=workdir,
        command=command,
        exit_code=completed_returncode,
        final_message=final_message,
        stdout=completed_stdout,
        stderr=completed_stderr,
        events=events,
        error="" if status == "ok" else first_nonempty_line(completed_stderr, completed_stdout),
    )


def _build_codex_command(codex_bin: str, task: WorkerTask, output_path: str) -> list[str]:
    mode = (task.mode or "implement").strip().lower()
    sandbox = "read-only" if mode in {"review", "analyze"} else "workspace-write"
    command = [
        codex_bin,
        "exec",
        "--json",
        "--full-auto",
        "--sandbox",
        sandbox,
        "--output-last-message",
        output_path,
    ]
    if task.model.strip():
        command.extend(["--model", task.model.strip()])
    command.append(task.build_prompt())
    return command


def _parse_jsonl_events(stdout: str) -> list[WorkerEvent]:
    events: list[WorkerEvent] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = str(payload.get("type", "event"))
        events.append(WorkerEvent(kind=kind, payload=payload))
    return events
def _read_text_if_exists(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    try:
        return p.read_text()
    except Exception:
        return ""


def _safe_unlink(path: str):
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass
