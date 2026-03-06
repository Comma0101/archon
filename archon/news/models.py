"""Data models for the AI news pipeline."""

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class NewsItem:
    """Normalized news item from an upstream source."""

    source: str
    title: str
    url: str
    score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "title": self.title,
            "url": self.url,
            "score": self.score,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NewsItem":
        return cls(
            source=str(data.get("source", "")),
            title=str(data.get("title", "")),
            url=str(data.get("url", "")),
            score=float(data.get("score", 0) or 0),
        )


@dataclass
class NewsDigest:
    """Rendered digest and metadata for a single run."""

    date_iso: str
    markdown: str
    used_fallback: bool
    item_count: int
    items: list[NewsItem] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "date": self.date_iso,
            "markdown": self.markdown,
            "used_fallback": self.used_fallback,
            "item_count": self.item_count,
            "items": [item.to_dict() for item in self.items],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NewsDigest":
        items = []
        raw_items = data.get("items") or []
        if isinstance(raw_items, list):
            items = [
                NewsItem.from_dict(item)
                for item in raw_items
                if isinstance(item, dict)
            ]
        return cls(
            date_iso=str(data.get("date") or data.get("date_iso") or ""),
            markdown=str(data.get("markdown") or ""),
            used_fallback=bool(data.get("used_fallback", False)),
            item_count=int(data.get("item_count", len(items)) or 0),
            items=items,
        )


@dataclass
class NewsRunResult:
    """Result object returned by the news runner."""

    status: Literal["sent", "built", "preview", "skipped", "no_news", "error"]
    reason: str = ""
    digest: NewsDigest | None = None

    def to_dict(self) -> dict:
        payload = {
            "status": self.status,
            "reason": self.reason,
        }
        if self.digest is not None:
            payload["digest"] = self.digest.to_dict()
        return payload
