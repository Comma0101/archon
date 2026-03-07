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


class TestDeepResearchTools:
    def test_check_research_job_uses_config_backed_refresh_client(self, monkeypatch):
        reg = make_registry()
        cfg = Config()
        cfg.llm.provider = "google"
        cfg.llm.api_key = "cfg-google-key"
        cfg.research.google_deep_research.enabled = True

        class _Record:
            interaction_id = "abc123"
            status = "completed"
            prompt = "agentic ai"
            updated_at = "2026-03-07T00:00:00Z"
            summary = "done"
            output_text = "report body"
            error = ""

        captured = {}

        monkeypatch.setattr("archon.tooling.content_tools.ensure_dirs", lambda: None)
        monkeypatch.setattr("archon.tooling.content_tools.load_config", lambda: cfg)
        monkeypatch.setattr(
            "archon.research.google_deep_research.GoogleDeepResearchClient.from_api_key",
            lambda api_key, agent=None: captured.update({"api_key": api_key, "agent": agent}) or object(),
        )
        monkeypatch.setattr(
            "archon.research.store.load_research_job",
            lambda interaction_id, refresh_client=None: captured.update(
                {"interaction_id": interaction_id, "refresh_client": refresh_client}
            ) or _Record(),
        )

        result = reg.execute("check_research_job", {"job_id": "research:abc123"})

        assert captured["api_key"] == "cfg-google-key"
        assert captured["interaction_id"] == "abc123"
        assert captured["refresh_client"] is not None
        assert "Job: research:abc123" in result
        assert "Status: completed" in result

    def test_list_research_jobs_tool_formats_recent_jobs(self, monkeypatch):
        reg = make_registry()
        cfg = Config()
        cfg.llm.provider = "google"
        cfg.llm.api_key = "cfg-google-key"
        cfg.research.google_deep_research.enabled = True

        class _Record:
            def __init__(self, interaction_id, status, summary):
                self.interaction_id = interaction_id
                self.status = status
                self.prompt = f"prompt for {interaction_id}"
                self.updated_at = "2026-03-07T00:00:00Z"
                self.summary = summary
                self.output_text = ""
                self.error = ""

        captured = {}

        monkeypatch.setattr("archon.tooling.content_tools.ensure_dirs", lambda: None)
        monkeypatch.setattr("archon.tooling.content_tools.load_config", lambda: cfg)
        monkeypatch.setattr(
            "archon.research.google_deep_research.GoogleDeepResearchClient.from_api_key",
            lambda api_key, agent=None: captured.update({"api_key": api_key}) or object(),
        )
        monkeypatch.setattr(
            "archon.research.store.list_research_jobs",
            lambda limit=20, refresh_client=None: captured.update(
                {"limit": limit, "refresh_client": refresh_client}
            ) or [
                _Record("job-1", "in_progress", "Running"),
                _Record("job-2", "completed", "Finished"),
            ],
        )

        result = reg.execute("list_research_jobs", {"limit": 2})

        assert captured["api_key"] == "cfg-google-key"
        assert captured["limit"] == 2
        assert captured["refresh_client"] is not None
        assert "Research jobs: 2" in result
        assert "research:job-1 | in_progress | Running" in result
        assert "research:job-2 | completed | Finished" in result
