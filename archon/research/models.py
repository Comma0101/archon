"""Persistent models for native research jobs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ResearchJobRecord:
    interaction_id: str
    status: str
    prompt: str
    agent: str
    created_at: str
    updated_at: str
    summary: str = ""
    output_text: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "interaction_id": self.interaction_id,
            "status": self.status,
            "prompt": self.prompt,
            "agent": self.agent,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "summary": self.summary,
            "output_text": self.output_text,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ResearchJobRecord":
        return cls(
            interaction_id=str(data.get("interaction_id", "") or ""),
            status=str(data.get("status", "") or ""),
            prompt=str(data.get("prompt", "") or ""),
            agent=str(data.get("agent", "") or ""),
            created_at=str(data.get("created_at", "") or ""),
            updated_at=str(data.get("updated_at", "") or ""),
            summary=str(data.get("summary", "") or ""),
            output_text=str(data.get("output_text", "") or ""),
            error=str(data.get("error", "") or ""),
        )
