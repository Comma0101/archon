"""Lightweight hook bus for control-plane lifecycle events."""

from __future__ import annotations

from collections import defaultdict
from threading import RLock
from typing import Callable

from archon.control.contracts import HookEvent


HookHandler = Callable[[HookEvent], None]


class HookBus:
    """In-process event bus with best-effort handler isolation."""

    def __init__(self):
        self._handlers: dict[str, list[HookHandler]] = defaultdict(list)
        self._lock = RLock()

    def register(self, kind: str, handler: HookHandler) -> None:
        key = (kind or "").strip() or "*"
        with self._lock:
            self._handlers[key].append(handler)

    def emit_kind(self, kind: str, *, task_id: str = "", payload: dict | None = None) -> None:
        self.emit(HookEvent(kind=(kind or "").strip(), task_id=task_id, payload=payload or {}))

    def emit(self, event: HookEvent) -> None:
        kind = (event.kind or "").strip()
        if not kind:
            return
        with self._lock:
            handlers = list(self._handlers.get(kind, ()))
            wildcard = list(self._handlers.get("*", ()))
        for handler in [*handlers, *wildcard]:
            try:
                handler(event)
            except Exception:
                # Hooks must never affect agent/tool execution.
                continue
