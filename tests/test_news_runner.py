"""Tests for the news runner orchestration."""

import datetime as dt
from types import SimpleNamespace

from archon.config import Config
from archon.llm import LLMResponse
from archon.news.models import NewsItem
import archon.news.runner as runner_module
from archon.news.runner import get_or_build_news_digest, run_news


class TestNewsRunner:
    def test_preview_returns_digest_even_when_disabled(self, tmp_path):
        config = Config()
        config.news.enabled = False

        def fake_fetch(_config):
            return [NewsItem(source="HN", title="LLM thing", url="https://x", score=100)]

        def fake_summarize(_config, items):
            assert len(items) == 1
            return "Preview digest body", False

        result = run_news(
            config,
            preview_only=True,
            now=dt.datetime(2026, 2, 24, 9, 0, 0),
            state_path=tmp_path / "state.json",
            fetch_items_fn=fake_fetch,
            summarize_fn=fake_summarize,
        )

        assert result.status == "preview"
        assert result.digest is not None
        assert "Preview digest body" in result.digest.markdown

    def test_run_skips_when_news_disabled(self):
        config = Config()
        config.news.enabled = False

        result = run_news(config, now=dt.datetime(2026, 2, 24, 9, 0, 0))

        assert result.status == "skipped"
        assert "news.disabled" in result.reason

    def test_run_respects_daily_gate(self, tmp_path):
        config = Config()
        config.news.enabled = True

        result = run_news(
            config,
            now=dt.datetime(2026, 2, 24, 7, 30, 0),
            state_path=tmp_path / "state.json",
        )

        assert result.status == "skipped"
        assert result.reason == "before_run_window"

    def test_run_reports_phase1_placeholder_after_gate(self, tmp_path):
        config = Config()
        config.news.enabled = True

        sent = []

        def fake_fetch(_config):
            return [NewsItem(source="HN", title="LLM thing", url="https://x", score=100)]

        def fake_summarize(_config, items):
            return f"Built digest for {len(items)} item(s)", False

        def fake_send(_config, text):
            sent.append(text)

        result = run_news(
            config,
            now=dt.datetime(2026, 2, 24, 9, 30, 0),
            state_path=tmp_path / "state.json",
            send_telegram=False,
            fetch_items_fn=fake_fetch,
            summarize_fn=fake_summarize,
            send_fn=fake_send,
        )

        assert result.status == "built"
        assert result.digest is not None
        assert "Built digest for 1 item(s)" in result.digest.markdown
        assert sent == []

    def test_run_sends_when_enabled(self, tmp_path):
        config = Config()
        config.news.enabled = True
        config.news.telegram.send_enabled = True

        sent = []

        result = run_news(
            config,
            now=dt.datetime(2026, 2, 24, 9, 30, 0),
            state_path=tmp_path / "state.json",
            send_telegram=True,
            fetch_items_fn=lambda _cfg: [
                NewsItem(source="HN", title="LLM thing", url="https://x", score=100)
            ],
            summarize_fn=lambda _cfg, _items: ("Live digest", False),
            send_fn=lambda _cfg, text: sent.append(text),
        )

        assert result.status == "sent"
        assert result.digest is not None
        assert sent and "Live digest" in sent[0]

    def test_run_returns_no_news_when_sources_empty(self, tmp_path):
        config = Config()
        config.news.enabled = True

        result = run_news(
            config,
            now=dt.datetime(2026, 2, 24, 9, 30, 0),
            state_path=tmp_path / "state.json",
            fetch_items_fn=lambda _cfg: [],
            summarize_fn=lambda _cfg, _items: ("unused", False),
        )

        assert result.status == "no_news"

    def test_get_or_build_reuses_cached_digest(self, tmp_path):
        config = Config()
        calls = {"fetch": 0}

        def fake_fetch(_cfg):
            calls["fetch"] += 1
            return [NewsItem(source="HN", title="LLM thing", url="https://x", score=100)]

        def fake_summarize(_cfg, _items):
            return "Cached me", False

        cache_path = tmp_path / "news" / "digest-2026-02-24.json"
        now = dt.datetime(2026, 2, 24, 9, 30, 0)

        first = get_or_build_news_digest(
            config,
            now=now,
            cache_path=cache_path,
            fetch_items_fn=fake_fetch,
            summarize_fn=fake_summarize,
            result_status="preview",
        )
        second = get_or_build_news_digest(
            config,
            now=now,
            cache_path=cache_path,
            fetch_items_fn=fake_fetch,
            summarize_fn=lambda *_args, **_kwargs: ("should not run", False),
            result_status="preview",
        )

        assert first.digest is not None
        assert second.digest is not None
        assert calls["fetch"] == 1
        assert second.reason == "cache_hit"
        assert "Cached me" in second.digest.markdown

    def test_get_or_build_records_usage_for_default_news_summarizer(self, monkeypatch, tmp_path):
        config = Config()
        recorded = []

        fake_llm = SimpleNamespace(
            provider="google",
            model="gemini-3.1-pro-preview",
        )
        fake_llm.chat = lambda _system_prompt, _messages, tools=None: LLMResponse(
            text="Summarized digest body",
            tool_calls=[],
            raw_content=[{"type": "text", "text": "Summarized digest body"}],
            input_tokens=12,
            output_tokens=4,
        )

        monkeypatch.setattr(runner_module, "_make_llm_client", lambda _cfg: fake_llm)
        monkeypatch.setattr(
            runner_module,
            "record_usage_event",
            lambda event: recorded.append(event) or True,
        )

        result = get_or_build_news_digest(
            config,
            now=dt.datetime(2026, 2, 24, 9, 30, 0),
            cache_path=tmp_path / "news" / "digest-2026-02-24.json",
            fetch_items_fn=lambda _cfg: [
                NewsItem(source="HN", title="LLM thing", url="https://x", score=100)
            ],
            result_status="preview",
        )

        assert result.digest is not None
        assert "Summarized digest body" in result.digest.markdown
        assert len(recorded) == 1
        assert recorded[0].session_id == "news-2026-02-24"
        assert recorded[0].turn_id == "2026-02-24"
        assert recorded[0].source == "news"
        assert recorded[0].provider == "google"
        assert recorded[0].model == "gemini-3.1-pro-preview"
        assert recorded[0].input_tokens == 12
        assert recorded[0].output_tokens == 4
