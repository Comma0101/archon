"""Claude Code CLI adapter for delegated coding tasks (Phase 1)."""

import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from archon.workers.base import WorkerEvent, WorkerExecObserver, WorkerResult, WorkerTask
from archon.workers.common import first_nonempty_line, summarize_cli_run
from archon.workers.subprocess_exec import run_streaming_process

_HELP_CACHE: dict[str, str] = {}


def claude_available() -> bool:
    return shutil.which("claude") is not None


def run_claude_code_task(task: WorkerTask, exec_observer: WorkerExecObserver | None = None) -> WorkerResult:
    claude_bin = shutil.which("claude")
    workdir = str(Path(task.repo_path).expanduser().resolve())
    if not claude_bin:
        return WorkerResult(
            worker="claude_code",
            status="unavailable",
            summary="Claude Code CLI not found on PATH",
            repo_path=workdir,
            error="Install Claude Code and ensure `claude` is available on PATH.",
        )
    if not Path(workdir).exists():
        return WorkerResult(
            worker="claude_code",
            status="error",
            summary="Repository path does not exist",
            repo_path=workdir,
            error=f"Not found: {workdir}",
        )
    if not Path(workdir).is_dir():
        return WorkerResult(
            worker="claude_code",
            status="error",
            summary="Repository path is not a directory",
            repo_path=workdir,
            error=f"Not a directory: {workdir}",
        )

    mode = (task.mode or "implement").strip().lower()
    broker_ctx = None
    if mode in {"implement", "debug"}:
        if not task.archon_session_id.strip():
            msg = (
                "Claude Code implement/debug requires an Archon worker session ID so approval requests can be recorded. "
                "Use `worker_start`/`worker_send` (or a delegating path that reserves a worker session) instead of calling the adapter directly."
            )
            return WorkerResult(
                worker="claude_code",
                status="unsupported",
                summary=msg,
                repo_path=workdir,
                error=msg,
            )
        if not _supports_permission_prompt_tool(claude_bin):
            msg = (
                "Claude Code CLI does not appear to support `--permission-prompt-tool` in this installed version. "
                "Upgrade Claude Code to use Archon's approval broker for implement/debug runs."
            )
            return WorkerResult(
                worker="claude_code",
                status="unsupported",
                summary=msg,
                repo_path=workdir,
                error=msg,
            )
        try:
            broker_ctx = _prepare_permission_broker(task)
        except Exception as e:
            return WorkerResult(
                worker="claude_code",
                status="error",
                summary=f"Failed to prepare Claude permission broker: {type(e).__name__}",
                repo_path=workdir,
                error=str(e),
            )

    command = _build_claude_command(claude_bin, task, broker_ctx=broker_ctx)
    timeout = max(1, int(task.timeout_sec))
    try:
        if exec_observer is None:
            completed = subprocess.run(
                command,
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=broker_ctx["env"] if broker_ctx else None,
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
                env=broker_ctx["env"] if broker_ctx else None,
                exec_observer=exec_observer,
            )
            completed_stdout = completed_stream.stdout
            completed_stderr = completed_stream.stderr
            completed_returncode = completed_stream.returncode or 0
            timed_out = completed_stream.timed_out
            cancelled = completed_stream.cancelled
    except subprocess.TimeoutExpired as e:
        _cleanup_broker(broker_ctx)
        return WorkerResult(
            worker="claude_code",
            status="timeout",
            summary=f"Timed out after {timeout}s",
            repo_path=workdir,
            command=command,
            stdout=(e.stdout or "") if isinstance(e.stdout, str) else "",
            stderr=(e.stderr or "") if isinstance(e.stderr, str) else "",
            error=f"Timeout after {timeout}s",
        )
    except Exception as e:
        _cleanup_broker(broker_ctx)
        return WorkerResult(
            worker="claude_code",
            status="error",
            summary=f"Failed to run Claude Code CLI: {type(e).__name__}",
            repo_path=workdir,
            command=command,
            error=str(e),
        )
    finally:
        _cleanup_broker(broker_ctx)

    if exec_observer is not None and timed_out:
        return WorkerResult(
            worker="claude_code",
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
            worker="claude_code",
            status="cancelled",
            summary="Delegated Claude Code task cancelled",
            repo_path=workdir,
            command=command,
            exit_code=completed_returncode,
            stdout=completed_stdout,
            stderr=completed_stderr,
            error="Cancelled by Archon runtime",
        )

    events = _parse_stream_json(completed_stdout)
    final_message = _extract_final_message(events, completed_stdout)
    vendor_session_id = _extract_vendor_session_id(events)
    status = "ok" if completed_returncode == 0 else "failed"

    return WorkerResult(
        worker="claude_code",
        status=status,
        summary=summarize_cli_run("Claude Code", status, completed_returncode, final_message, completed_stderr),
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


def _build_claude_command(claude_bin: str, task: WorkerTask, broker_ctx: dict | None = None) -> list[str]:
    permission_mode = "dontAsk"
    if broker_ctx is not None:
        permission_mode = "default"
    command = [
        claude_bin,
        "-p",
        task.build_prompt(),
        "--output-format",
        "stream-json",
        "--permission-mode",
        permission_mode,
        "--no-chrome",
        "--max-turns",
        "8",
    ]
    if broker_ctx is not None:
        command.extend(["--mcp-config", broker_ctx["mcp_config_path"]])
        command.extend(["--permission-prompt-tool", broker_ctx["permission_tool_name"]])
    if task.resume_vendor_session_id.strip():
        command.extend(["--resume", task.resume_vendor_session_id.strip()])
    if task.model.strip():
        command.extend(["--model", task.model.strip()])
    return command


def _supports_permission_prompt_tool(claude_bin: str) -> bool:
    help_text = _get_claude_help(claude_bin)
    return "--permission-prompt-tool" in help_text


def _get_claude_help(claude_bin: str) -> str:
    cached = _HELP_CACHE.get(claude_bin)
    if cached is not None:
        return cached
    try:
        proc = subprocess.run(
            [claude_bin, "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        text = (proc.stdout or "") + (proc.stderr or "")
    except Exception:
        text = ""
    _HELP_CACHE[claude_bin] = text
    return text


def _prepare_permission_broker(task: WorkerTask) -> dict:
    repo_root = str(Path(__file__).resolve().parents[2])
    env = dict(os.environ)
    env["ARCHON_WORKER_SESSION_ID"] = task.archon_session_id
    env.setdefault("ARCHON_WORKER_APPROVAL_TIMEOUT_SEC", str(max(60, int(task.timeout_sec))))
    env.setdefault("ARCHON_WORKER_APPROVAL_POLL_SEC", "0.5")
    env["PYTHONUNBUFFERED"] = "1"
    pythonpath_parts = [repo_root]
    if env.get("PYTHONPATH"):
        pythonpath_parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)

    config = {
        "mcpServers": {
            "archon_approval": {
                "type": "stdio",
                "command": sys.executable,
                "args": ["-m", "archon.workers.claude_permission_mcp"],
                "env": {
                    "ARCHON_WORKER_SESSION_ID": task.archon_session_id,
                    "ARCHON_WORKER_APPROVAL_TIMEOUT_SEC": str(max(60, int(task.timeout_sec))),
                    "ARCHON_WORKER_APPROVAL_POLL_SEC": "0.5",
                    "PYTHONUNBUFFERED": "1",
                    "PYTHONPATH": env["PYTHONPATH"],
                },
            }
        }
    }
    tmp = tempfile.NamedTemporaryFile(prefix="archon-claude-mcp-", suffix=".json", delete=False)
    tmp.write(json.dumps(config).encode("utf-8"))
    tmp.close()
    _register_temp_cleanup(tmp.name)
    return {
        "env": env,
        "mcp_config_path": tmp.name,
        "permission_tool_name": "mcp__archon_approval__permission_prompt",
    }


def _cleanup_broker(broker_ctx: dict | None):
    if not broker_ctx:
        return
    path = broker_ctx.get("mcp_config_path")
    if not isinstance(path, str) or not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass


def _register_temp_cleanup(path: str):
    def _cleanup():
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:
            pass

    atexit.register(_cleanup)


def _parse_stream_json(stdout: str) -> list[WorkerEvent]:
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
        if event.kind in {"result", "assistant_message", "message", "assistant"}:
            text = _extract_text(payload)
            if text:
                candidates.append(text)
        elif event.kind.endswith(".completed") or event.kind.endswith(".finished"):
            text = _extract_text(payload)
            if text:
                candidates.append(text)

    if candidates:
        return "\n\n".join(c.strip() for c in candidates if c.strip()).strip()

    non_json_lines = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            json.loads(line)
        except json.JSONDecodeError:
            non_json_lines.append(line)
    return "\n".join(non_json_lines).strip()


def _extract_vendor_session_id(events: list[WorkerEvent]) -> str:
    keys = ("session_id", "sessionId", "id")
    for event in events:
        payload = event.payload
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value and _looks_like_session_id(value):
                return value
        session = payload.get("session")
        if isinstance(session, dict):
            for key in keys:
                value = session.get(key)
                if isinstance(value, str) and value:
                    return value
    return ""


def _looks_like_session_id(value: str) -> bool:
    return len(value.strip()) >= 1


def _extract_text(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("result", "text", "message", "content", "completion"):
            if key in value:
                text = _extract_text(value[key])
                if text:
                    return text
        for v in value.values():
            text = _extract_text(v)
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

