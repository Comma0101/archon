"""Tests for web page reading and extraction helpers."""

import pytest

from archon.config import Config
from archon.web.read import read_web_url


class TestWebRead:
    def test_reads_html_and_extracts_title_and_text(self):
        cfg = Config()
        payload = {
            "body": b"<html><head><title>Example</title></head><body><h1>Hello</h1><p>World</p><script>x</script></body></html>",
            "final_url": "https://example.com/final",
            "content_type": "text/html; charset=utf-8",
            "charset": "utf-8",
        }

        page = read_web_url(
            "https://example.com",
            config=cfg,
            fetch_fn=lambda *_args, **_kwargs: payload,
        )

        assert page.title == "Example"
        assert page.final_url == "https://example.com/final"
        assert "Hello World" in page.text
        assert "script" not in page.text.lower()

    def test_reads_plain_text(self):
        page = read_web_url(
            "https://example.com/file.txt",
            fetch_fn=lambda *_args, **_kwargs: {
                "body": b"plain text file",
                "final_url": "https://example.com/file.txt",
                "content_type": "text/plain",
                "charset": "utf-8",
            },
        )
        assert page.title == ""
        assert page.text == "plain text file"

    def test_blocks_localhost(self):
        with pytest.raises(ValueError):
            read_web_url("http://localhost:8080")
