"""Filtering, deduplication, and ranking pipeline for news digests."""

from __future__ import annotations

from archon.news.models import NewsItem


def dedupe_items(items: list[NewsItem]) -> list[NewsItem]:
    """Deduplicate items by URL while preserving first-seen order."""
    seen_urls: set[str] = set()
    deduped: list[NewsItem] = []
    for item in items:
        url = (item.url or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        deduped.append(item)
    return deduped


def apply_thresholds(items: list[NewsItem], config) -> list[NewsItem]:
    """Apply source-specific score thresholds to reduce noise."""
    out: list[NewsItem] = []
    for item in items:
        if item.source == "GitHub" and item.score < config.news.min_github_stars:
            continue
        if item.source == "HN" and item.score < config.news.min_hn_score:
            continue
        if item.source == "Reddit" and item.score < config.news.min_reddit_score:
            continue
        out.append(item)
    return out


def rank_items(items: list[NewsItem]) -> list[NewsItem]:
    """Sort descending by score with source/title tie-breakers for determinism."""
    return sorted(
        items,
        key=lambda i: (float(i.score), i.source, i.title),
        reverse=True,
    )


def prefilter_items(items: list[NewsItem], config) -> list[NewsItem]:
    """Apply thresholds, dedupe, and cap to pre-LLM context budget."""
    filtered = apply_thresholds(items, config)
    filtered = dedupe_items(filtered)
    ranked = rank_items(filtered)
    cap = max(1, int(config.news.prefilter_cap))
    return ranked[:cap]


def select_digest_items(items: list[NewsItem], config) -> list[NewsItem]:
    """Select top items for the final digest."""
    ranked = rank_items(items)
    max_items = max(1, int(config.news.max_items))
    return ranked[:max_items]
