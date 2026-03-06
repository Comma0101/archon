"""Tests for tool registry and built-in tools."""

import os
import tempfile
from pathlib import Path

from archon.config import Config
from archon.news.models import NewsDigest
from archon.news.models import NewsRunResult
from archon.tools import ToolRegistry
from archon.workers.base import WorkerEvent, WorkerResult, WorkerTask
from archon.workers.runtime import ActiveWorkerRun
from archon.workers.session_store import WorkerSessionRecord


def make_registry():
    return ToolRegistry(archon_source_dir=None)

class TestNewsBriefTool:
    def test_returns_cached_or_built_digest_text(self, monkeypatch):
        reg = make_registry()
        cfg = Config()

        monkeypatch.setattr("archon.tooling.content_tools.ensure_dirs", lambda: None)
        monkeypatch.setattr("archon.tooling.content_tools.load_config", lambda: cfg)
        monkeypatch.setattr(
            "archon.tooling.content_tools.get_or_build_news_digest",
            lambda _cfg, force_refresh=False: NewsRunResult(
                status="built" if force_refresh else "built",
                reason="cache_hit" if not force_refresh else "",
                digest=NewsDigest(
                    date_iso="2026-02-24",
                    markdown="Digest body",
                    used_fallback=False,
                    item_count=3,
                    items=[],
                ),
            ),
        )

        result = reg.execute("news_brief", {})
        assert "news status:" in result
        assert "Digest body" in result
        assert "cache_hit" in result

    def test_can_request_telegram_delivery(self, monkeypatch):
        reg = make_registry()
        cfg = Config()
        cfg.news.telegram.send_enabled = True
        sent = []

        monkeypatch.setattr("archon.tooling.content_tools.ensure_dirs", lambda: None)
        monkeypatch.setattr("archon.tooling.content_tools.load_config", lambda: cfg)
        monkeypatch.setattr(
            "archon.tooling.content_tools.get_or_build_news_digest",
            lambda _cfg, force_refresh=False: NewsRunResult(
                status="built",
                reason="",
                digest=NewsDigest(
                    date_iso="2026-02-24",
                    markdown="Digest body",
                    used_fallback=True,
                    item_count=1,
                    items=[],
                ),
            ),
        )
        monkeypatch.setattr(
            "archon.tooling.content_tools.send_digest_to_telegram",
            lambda _cfg, text: sent.append(text),
        )

        result = reg.execute("news_brief", {"send_to_telegram": True})
        assert sent == ["Digest body"]
        assert "telegram_delivery: sent" in result

class TestWebTools:
    def test_web_search_tool_formats_results(self, monkeypatch):
        reg = make_registry()
        cfg = Config()

        monkeypatch.setattr("archon.tooling.content_tools.ensure_dirs", lambda: None)
        monkeypatch.setattr("archon.tooling.content_tools.load_config", lambda: cfg)
        monkeypatch.setattr(
            "archon.tooling.content_tools.search_web",
            lambda **kwargs: (
                [
                    type("R", (), {
                        "title": "Test Result",
                        "url": "https://example.com/x",
                        "snippet": "Snippet text",
                        "source": "duckduckgo",
                    })()
                ],
                {"provider": "duckduckgo_html", "notes": []},
            ),
        )

        result = reg.execute("web_search", {"query": "test"})
        assert "web_search provider: duckduckgo_html" in result
        assert "Test Result" in result
        assert "https://example.com/x" in result

    def test_web_read_tool_formats_page(self, monkeypatch):
        reg = make_registry()
        cfg = Config()

        monkeypatch.setattr("archon.tooling.content_tools.ensure_dirs", lambda: None)
        monkeypatch.setattr("archon.tooling.content_tools.load_config", lambda: cfg)
        monkeypatch.setattr(
            "archon.tooling.content_tools.read_web_url",
            lambda url, config, max_chars=6000: type("P", (), {
                "url": url,
                "final_url": url,
                "content_type": "text/html",
                "title": "Example",
                "text": "Body text",
            })(),
        )

        result = reg.execute("web_read", {"url": "https://example.com"})
        assert "Title: Example" in result
        assert "Body text" in result
