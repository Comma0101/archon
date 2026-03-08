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
    def test_check_research_job_reads_local_stream_state(self, monkeypatch):
        reg = make_registry()
        cfg = Config()
        cfg.llm.provider = "google"
        cfg.llm.api_key = "cfg-google-key"
        cfg.research.google_deep_research.enabled = True

        class _Record:
            interaction_id = "abc123"
            status = "completed"
            prompt = "agentic ai"
            created_at = "2026-03-07T00:00:00Z"
            updated_at = "2026-03-07T00:00:00Z"
            summary = "done"
            output_text = "report body"
            error = ""
            provider_status = "completed"
            last_polled_at = "2026-03-07T00:01:00Z"
            last_event_at = "2026-03-07T00:01:05Z"
            stream_status = "interaction.complete"
            latest_thought_summary = "Checking sources"
            poll_count = 3
            timeout_minutes = 20
            _refresh_attempted = True
            _refresh_ok = True
            _refresh_error = ""

        monkeypatch.setattr("archon.tooling.content_tools.ensure_dirs", lambda: None)
        monkeypatch.setattr("archon.tooling.content_tools.load_config", lambda: cfg)
        monkeypatch.setattr(
            "archon.research.store.load_research_job",
            lambda interaction_id, refresh_client=None: _Record(),
        )

        result = reg.execute("check_research_job", {"job_id": "research:abc123"})

        assert "job_id: research:abc123" in result
        assert "job_status: completed" in result
        assert "job_provider_status: completed" in result
        assert "job_last_polled_at: 2026-03-07T00:01:00Z" in result
        assert "job_poll_count: 3" in result
        assert "job_live_status:" in result

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
                self.created_at = "2026-03-07T00:00:00Z"
                self.updated_at = "2026-03-07T00:00:00Z"
                self.summary = summary
                self.output_text = ""
                self.error = ""
                self.provider_status = status
                self.last_polled_at = "2026-03-07T00:01:00Z"
                self.last_event_at = "2026-03-07T00:01:05Z"
                self.stream_status = "content.delta"
                self.latest_thought_summary = "Running"
                self.poll_count = 2
                self.timeout_minutes = 20
                self._refresh_attempted = True
                self._refresh_ok = True
                self._refresh_error = ""

        monkeypatch.setattr("archon.tooling.content_tools.ensure_dirs", lambda: None)
        monkeypatch.setattr("archon.tooling.content_tools.load_config", lambda: cfg)
        monkeypatch.setattr(
            "archon.research.store.list_research_jobs",
            lambda limit=20, refresh_client=None: [
                _Record("job-1", "in_progress", "Running"),
                _Record("job-2", "completed", "Finished"),
            ],
        )

        result = reg.execute("list_research_jobs", {"limit": 2})

        assert "Research jobs: 2" in result
        assert "research:job-1 | in_progress | provider=in_progress | events=2" in result
        assert "research:job-2 | completed | provider=completed | events=2" in result
