"""Control-plane contracts for orchestrator/execution migration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TaskSpec:
    """Normalized control-plane task request."""

    task_id: str
    intent: str
    mode: str = "implement"
    profile: str = "default"
    payload: dict[str, Any] = field(default_factory=dict)
    constraints: str = ""
    context_ref: str = ""


@dataclass
class CapabilityProfile:
    """Declarative capability surface for a routed task/agent."""

    name: str = "default"
    allowed_tools: list[str] = field(default_factory=lambda: ["*"])
    max_mode: str = "implement"
    execution_backend: str = "host"


@dataclass
class HookEvent:
    """Lifecycle event envelope emitted by control-plane hooks."""

    kind: str
    task_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionRequest:
    """Control-plane request sent to execution plane."""

    task: TaskSpec
    worker: str = "auto"
    timeout_sec: int = 900
    repo_path: str = "."


@dataclass
class ExecutionResult:
    """Execution-plane response normalized for control-plane consumers."""

    status: str
    summary: str
    worker: str = ""
    details: dict[str, Any] = field(default_factory=dict)
