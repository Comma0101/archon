"""Shared lightweight activity event payloads for assistant UX surfaces."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ActivityEvent:
    """Compact activity notice that can be rendered across UX surfaces."""

    source: str
    message: str

    def render_text(self) -> str:
        source = (self.source or "activity").strip() or "activity"
        message = (self.message or "").strip() or "(empty)"
        return f"[{source}] {message}"
