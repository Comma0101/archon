"""Execution-plane runtime contracts for migration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExecutionBackendInfo:
    """Static description of an execution backend."""

    name: str = "host"
    sandboxed: bool = False
    supports_network_control: bool = False


@dataclass
class ExecutionRuntimeResult:
    """Execution runtime outcome envelope."""

    status: str
    summary: str
    exit_code: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SuspensionRequest:
    """Structured request to pause work and wait for external input."""

    question: str
    kind: str = ""
    reason: str = ""
    job_id: str = ""
    project: str = ""
    context: str = ""
    resume_hint: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def format_suspension_request(request: SuspensionRequest) -> str:
    """Render a human-readable summary for a suspension request."""
    lines = ["Human input needed", request.question]
    if request.context:
        lines.append(request.context)
    if request.project:
        lines.append(f"Project: {request.project}")
    if request.resume_hint:
        lines.append(f"Resume: {request.resume_hint}")
    return "\n".join(lines)
