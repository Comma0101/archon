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
    provider_status: str = ""
    last_polled_at: str = ""
    last_event_at: str = ""
    last_event_id: str = ""
    stream_status: str = ""
    latest_thought_summary: str = ""
    poll_count: int = 0
    timeout_minutes: int = 20

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
            "provider_status": self.provider_status,
            "last_polled_at": self.last_polled_at,
            "last_event_at": self.last_event_at,
            "last_event_id": self.last_event_id,
            "stream_status": self.stream_status,
            "latest_thought_summary": self.latest_thought_summary,
            "poll_count": int(self.poll_count or 0),
            "timeout_minutes": int(self.timeout_minutes or 20),
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
            provider_status=str(data.get("provider_status", "") or ""),
            last_polled_at=str(data.get("last_polled_at", "") or ""),
            last_event_at=str(data.get("last_event_at", "") or ""),
            last_event_id=str(data.get("last_event_id", "") or ""),
            stream_status=str(data.get("stream_status", "") or ""),
            latest_thought_summary=str(data.get("latest_thought_summary", "") or ""),
            poll_count=int(data.get("poll_count", 0) or 0),
            timeout_minutes=int(data.get("timeout_minutes", 20) or 20),
        )
