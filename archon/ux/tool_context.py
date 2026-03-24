"""Lightweight execution context for tool handlers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolContext:
    """Passed as ``_ctx`` to opted-in tool handlers.

    Handlers can write structured metadata to ``meta`` and optionally emit
    UX events during execution.
    """

    tool_name: str
    session_id: str
    emit: Callable[[Any], None]
    meta: dict[str, Any] = field(default_factory=dict)
