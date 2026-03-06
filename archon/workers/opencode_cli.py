"""OpenCode CLI adapter for delegated coding tasks (one-shot Phase 1)."""

import json
import shutil
import subprocess
from pathlib import Path

from archon.workers.base import WorkerEvent, WorkerExecObserver, WorkerResult, WorkerTask
from archon.workers.common import first_nonempty_line, summarize_cli_run
from archon.workers.subprocess_exec import run_streaming_process


def opencode_available() -> bool:
    return shutil.which("opencode") is not None


def run_opencode_task(task: WorkerTask, exec_observer: WorkerExecObserver | None = None) -> WorkerResult:
    opencode_bin = shutil.which("opencode")
    workdir = str(Path(task.repo_path).expanduser().resolve())
    if not opencode_bin:
        return WorkerResult(
            worker="opencode",
            status="unavailable",
            summary="OpenCode CLI not found on PATH",
            repo_path=workdir,
            error="Install OpenCode and ensure `opencode` is available on PATH.",
        )
    if not Path(workdir).exists():
        return WorkerResult(
            worker="opencode",
            status="error",
            summary="Repository path does not exist",
            repo_path=workdir,
            error=f"Not found: {workdir}",
        )
    if not Path(workdir).is_dir():
        return WorkerResult(
            worker="opencode",
            status="error",
            summary="Repository path is not a directory",
            repo_path=workdir,
            error=f"Not a directory: {workdir}",
        )

    command = _build_opencode_command(opencode_bin, task)
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
        return WorkerResult(
            worker="opencode",
            status="timeout",
            summary=f"Timed out after {timeout}s",
            repo_path=workdir,
            command=command,
            stdout=(e.stdout or "") if isinstance(e.stdout, str) else "",
            stderr=(e.stderr or "") if isinstance(e.stderr, str) else "",
            error=f"Timeout after {timeout}s",
        )
    except Exception as e:
        return WorkerResult(
            worker="opencode",
            status="error",
            summary=f"Failed to run OpenCode CLI: {type(e).__name__}",
            repo_path=workdir,
            command=command,
            error=str(e),
        )

    if exec_observer is not None and timed_out:
        return WorkerResult(
            worker="opencode",
            status="timeout",
            summary=f"Timed out after {timeout}s",
            repo_path=workdir,
            command=command,
            stdout=completed_stdout,
            stderr=completed_stderr,
            error=f"Timeout after {timeout}s",
        )
    if exec_observer is not None and cancelled:
        return WorkerResult(
            worker="opencode",
            status="cancelled",
            summary="Delegated OpenCode task cancelled",
            repo_path=workdir,
            command=command,
            exit_code=completed_returncode,
            stdout=completed_stdout,
            stderr=completed_stderr,
            error="Cancelled by Archon runtime",
        )

    events = _parse_json_events(completed_stdout)
    final_message = _extract_final_message(events, completed_stdout)
    vendor_session_id = _extract_vendor_session_id(events)
    status = "ok" if completed_returncode == 0 else "failed"

    return WorkerResult(
        worker="opencode",
        status=status,
        summary=summarize_cli_run("OpenCode", status, completed_returncode, final_message, completed_stderr),
        repo_path=workdir,
        command=command,
        exit_code=completed_returncode,
        final_message=final_message,
        stdout=completed_stdout,
        stderr=completed_stderr,
        events=events,
        error="" if status == "ok" else first_nonempty_line(completed_stderr, completed_stdout),
        vendor_session_id=vendor_session_id,
    )


def _build_opencode_command(opencode_bin: str, task: WorkerTask) -> list[str]:
    command = [
        opencode_bin,
        "run",
        "--format",
        "json",
    ]
    if task.resume_vendor_session_id.strip():
        command.extend(["--session", task.resume_vendor_session_id.strip()])
    if task.model.strip():
        command.extend(["--model", task.model.strip()])
    command.append(task.build_prompt())
    return command


def _parse_json_events(stdout: str) -> list[WorkerEvent]:
    events: list[WorkerEvent] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        kind = str(payload.get("type") or payload.get("event") or "event")
        events.append(WorkerEvent(kind=kind, payload=payload))
    return events


def _extract_final_message(events: list[WorkerEvent], stdout: str) -> str:
    candidates: list[str] = []
    for event in events:
        payload = event.payload
        if event.kind in {"result", "assistant", "assistant_message", "message", "message.completed", "text"}:
            text = _extract_text(payload)
            if text:
                candidates.append(text)
        elif event.kind.endswith(".completed") or event.kind.endswith(".finished"):
            text = _extract_text(payload)
            if text:
                candidates.append(text)
    if candidates:
        return "\n\n".join(c.strip() for c in candidates if c.strip()).strip()

    non_json_lines: list[str] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            json.loads(stripped)
        except json.JSONDecodeError:
            non_json_lines.append(stripped)
    return "\n".join(non_json_lines).strip()


def _extract_vendor_session_id(events: list[WorkerEvent]) -> str:
    for event in events:
        payload = event.payload
        for key in ("session_id", "sessionId", "sessionID", "id"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        session = payload.get("session")
        if isinstance(session, dict):
            for key in ("session_id", "sessionId", "sessionID", "id"):
                value = session.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        part = payload.get("part")
        if isinstance(part, dict):
            for key in ("session_id", "sessionId", "sessionID", "id"):
                value = part.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return ""


def _extract_text(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("text", "message", "content", "result", "output", "completion", "part"):
            if key in value:
                text = _extract_text(value[key])
                if text:
                    return text
        for item in value.values():
            text = _extract_text(item)
            if text:
                return text
        return ""
    if isinstance(value, list):
        parts = []
        for item in value:
            text = _extract_text(item)
            if text:
                parts.append(text)
        return "\n".join(parts).strip()
    return ""

