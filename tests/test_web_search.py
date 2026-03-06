"""Tests for web search backends and parsing."""

from archon.config import Config
from archon.web.search import search_web


class TestWebSearch:
    def test_duckduckgo_html_parser_extracts_results(self):
        cfg = Config()
        cfg.web.provider = "duckduckgo_html"
        page_html = """
        <html><body>
          <a class="result__a" href="/l/?uddg=https%3A%2F%2Fexample.com%2Fa">Alpha Result</a>
          <div class="result__snippet">Alpha snippet</div>
          <a class="result__a" href="https://example.org/b">Beta Result</a>
          <div class="result__snippet">Beta snippet</div>
        </body></html>
        """

        results, meta = search_web(
            "alpha",
            config=cfg,
            fetch_text_fn=lambda *_args, **_kwargs: page_html,
        )

        assert meta["provider"] == "duckduckgo_html"
        assert len(results) == 2
        assert results[0].title == "Alpha Result"
        assert results[0].url == "https://example.com/a"
        assert "Alpha snippet" in results[0].snippet

    def test_domain_filter_is_applied(self):
        cfg = Config()

        # Exercise the public filter path using SearxNG branch to avoid HTML parsing dependency here.
        cfg.web.provider = "searxng"
        cfg.web.searxng_base_url = "https://searx.example"

        results, meta = search_web(
            "python",
            config=cfg,
            domains=["python.org"],
            fetch_json_fn=lambda *_args, **_kwargs: {
                "results": [
                    {"title": "Docs", "url": "https://docs.python.org/3/", "content": "Python docs", "engine": "searx"},
                    {"title": "Other", "url": "https://example.com/", "content": "Other", "engine": "searx"},
                ]
            },
        )

        assert len(results) == 1
        assert results[0].url.startswith("https://docs.python.org")
        assert any("domain_filter_applied" in note for note in meta["notes"])

    def test_empty_query_returns_no_results(self):
        results, meta = search_web("   ", config=Config())
        assert results == []
        assert meta["provider"] == "none"

    def test_searxng_falls_back_to_duckduckgo_when_backend_errors(self):
        cfg = Config()
        cfg.web.provider = "searxng"
        cfg.web.searxng_base_url = "https://searx.example"
        page_html = """
        <html><body>
          <a class="result__a" href="https://example.com/a">Alpha Result</a>
          <div class="result__snippet">Alpha snippet</div>
        </body></html>
        """

        def _broken_fetch_json(*_args, **_kwargs):
            raise RuntimeError("backend unavailable")

        results, meta = search_web(
            "alpha",
            config=cfg,
            fetch_json_fn=_broken_fetch_json,
            fetch_text_fn=lambda *_args, **_kwargs: page_html,
        )

        assert meta["provider"] == "duckduckgo_html"
        assert any("searxng_error" in note for note in meta["notes"])
        assert len(results) == 1
        assert results[0].url == "https://example.com/a"

    def test_auto_provider_prefers_searxng_when_available(self):
        cfg = Config()
        cfg.web.provider = "auto"
        cfg.web.searxng_base_url = "https://searx.example"

        results, meta = search_web(
            "python",
            config=cfg,
            fetch_json_fn=lambda *_args, **_kwargs: {
                "results": [
                    {
                        "title": "Docs",
                        "url": "https://docs.python.org/3/",
                        "content": "Python docs",
                        "engine": "searx",
                    }
                ]
            },
            fetch_text_fn=lambda *_args, **_kwargs: "",
        )

        assert meta["provider"] == "searxng"
        assert len(results) == 1
        assert results[0].url.startswith("https://docs.python.org")
