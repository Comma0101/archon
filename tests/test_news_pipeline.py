"""Tests for news filtering/dedupe/ranking pipeline."""

from archon.config import Config
from archon.news.models import NewsItem
from archon.news.pipeline import (
    apply_thresholds,
    dedupe_items,
    prefilter_items,
    select_digest_items,
)


def _items():
    return [
        NewsItem(source="HN", title="HN low", url="https://a", score=5),
        NewsItem(source="HN", title="HN high", url="https://b", score=50),
        NewsItem(source="GitHub", title="GH low", url="https://c", score=10),
        NewsItem(source="GitHub", title="GH high", url="https://d", score=500),
        NewsItem(source="Reddit", title="RD low", url="https://e", score=2),
        NewsItem(source="Reddit", title="RD high", url="https://f", score=42),
        NewsItem(source="HF", title="Paper", url="https://g", score=100),
        NewsItem(source="HF", title="Paper dup url", url="https://g", score=99),
    ]


class TestNewsPipeline:
    def test_apply_thresholds_uses_source_specific_limits(self):
        cfg = Config()
        out = apply_thresholds(_items(), cfg)
        titles = {i.title for i in out}
        assert "HN low" not in titles
        assert "GH low" not in titles
        assert "RD low" not in titles
        assert "HN high" in titles
        assert "GH high" in titles
        assert "RD high" in titles
        assert "Paper" in titles

    def test_dedupe_items_removes_duplicate_urls(self):
        out = dedupe_items(_items())
        urls = [i.url for i in out]
        assert urls.count("https://g") == 1

    def test_prefilter_items_applies_thresholds_dedupe_and_cap(self):
        cfg = Config()
        cfg.news.prefilter_cap = 3
        out = prefilter_items(_items(), cfg)
        assert len(out) == 3
        scores = [i.score for i in out]
        assert scores == sorted(scores, reverse=True)

    def test_select_digest_items_respects_max_items(self):
        cfg = Config()
        cfg.news.max_items = 2
        out = select_digest_items(prefilter_items(_items(), cfg), cfg)
        assert len(out) == 2

