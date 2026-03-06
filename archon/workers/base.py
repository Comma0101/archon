"""Common data models for delegated coding worker runs."""

from dataclasses import dataclass, field
from typing import Any, Protocol

from archon.workers.common import truncate_report


@dataclass
class WorkerTask:
    task: str
    worker: str = "auto"
    mode: str = "implement"  # analyze | review | implement | debug
    repo_path: str = "."
    timeout_sec: int = 900
    constraints: str = ""
    model: str = ""
    resume_vendor_session_id: str = ""
    archon_session_id: str = ""

    def build_prompt(self) -> str:
        lines = [self.task.strip()]
        if self.constraints.strip():
            lines.extend(["", "Constraints:", self.constraints.strip()])
        return "\n".join(line for line in lines if line is not None).strip()

    def to_dict(self) -> dict:
        return {
            "task": self.task,
            "worker": self.worker,
            "mode": self.mode,
            "repo_path": self.repo_path,
            "timeout_sec": self.timeout_sec,
            "constraints": self.constraints,
            "model": self.model,
            "resume_vendor_session_id": self.resume_vendor_session_id,
            "archon_session_id": self.archon_session_id,
        }


@dataclass
class WorkerEvent:
    kind: str
    payload: dict

    def to_dict(self) -> dict:
        return {"kind": self.kind, "payload": self.payload}

    @classmethod
    def from_dict(cls, data: dict) -> "WorkerEvent":
        return cls(
            kind=str(data.get("kind", "event")),
            payload=dict(data.get("payload", {})),
        )


@dataclass
class WorkerResult:
    worker: str
    status: str  # ok | failed | timeout | cancelled | unavailable | unsupported | error
    summary: str = ""
    repo_path: str = ""
    command: list[str] = field(default_factory=list)
    exit_code: int | None = None
    final_message: str = ""
    stdout: str = ""
    stderr: str = ""
    events: list[WorkerEvent] = field(default_factory=list)
    error: str = ""
    vendor_session_id: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def to_dict(self, include_output: bool = True) -> dict:
        data = {
            "worker": self.worker,
            "status": self.status,
            "summary": self.summary,
            "repo_path": self.repo_path,
            "command": list(self.command),
            "exit_code": self.exit_code,
            "final_message": self.final_message,
            "events": [e.to_dict() for e in self.events],
            "error": self.error,
            "vendor_session_id": self.vendor_session_id,
        }
        if include_output:
            data["stdout"] = self.stdout
            data["stderr"] = self.stderr
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "WorkerResult":
        events = [WorkerEvent.from_dict(e) for e in data.get("events", []) or []]
        return cls(
            worker=str(data.get("worker", "")),
            status=str(data.get("status", "error")),
            summary=str(data.get("summary", "")),
            repo_path=str(data.get("repo_path", "")),
            command=[str(x) for x in (data.get("command", []) or [])],
            exit_code=data.get("exit_code"),
            final_message=str(data.get("final_message", "")),
            stdout=str(data.get("stdout", "")),
            stderr=str(data.get("stderr", "")),
            events=events,
            error=str(data.get("error", "")),
            vendor_session_id=str(data.get("vendor_session_id", "")),
        )


def format_worker_result(result: WorkerResult, max_chars: int = 6000) -> str:
    """Render a normalized worker result for the agent tool loop."""
    lines = [
        f"worker: {result.worker}",
        f"status: {result.status}",
    ]
    if result.summary:
        lines.append(f"summary: {result.summary}")
    if result.repo_path:
        lines.append(f"repo_path: {result.repo_path}")
    if result.exit_code is not None:
        lines.append(f"exit_code: {result.exit_code}")
    if result.events:
        lines.append(f"events: {len(result.events)}")
    if result.command:
        lines.append("command: " + " ".join(result.command))
    if result.error:
        lines.append(f"error: {result.error}")

    if result.final_message.strip():
        lines.extend(["", "final_message:", truncate_report(result.final_message.strip(), max_chars)])

    extras = []
    if result.stderr.strip():
        extras.append(("stderr", result.stderr.strip()))
    if result.stdout.strip() and not result.final_message.strip():
        extras.append(("stdout", result.stdout.strip()))
    for label, text in extras:
        lines.extend(["", f"{label}:", truncate_report(text, max_chars)])

    return "\n".join(lines)


class WorkerExecObserver(Protocol):
    """Optional observer used by adapters to report process lifecycle and streaming output."""

    def on_process_started(self, process: Any) -> None: ...

    def on_process_output(self, stream: str, text: str) -> None: ...

    def on_process_exit(self, returncode: int | None) -> None: ...

    def on_process_signal(self, signal_name: str, reason: str = "") -> None: ...

    def is_cancel_requested(self) -> bool: ...
