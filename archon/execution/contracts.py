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
