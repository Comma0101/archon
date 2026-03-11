"""Tests for news summarization helpers."""

from dataclasses import dataclass

from archon.config import Config
from archon.llm import LLMResponse
from archon.news.models import NewsItem
from archon.news.summarize import build_fallback_digest, build_news_prompt, summarize_with_llm


@dataclass
class FakeLLM:
    responses: list
    provider: str = "google"
    model: str = "gemini-3.1-pro-preview"

    def chat(self, system_prompt, messages, tools=None):
        assert system_prompt
        assert messages and messages[0]["role"] == "user"
        assert tools is None
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def _items():
    return [
        NewsItem(source="HN", title="LLM launch", url="https://a", score=100),
        NewsItem(source="GitHub", title="Repo", url="https://b", score=90),
    ]


class TestNewsSummarize:
    def test_build_news_prompt_lists_items(self):
        prompt = build_news_prompt(_items())
        assert "[HN] LLM launch" in prompt
        assert "[GitHub] Repo" in prompt

    def test_build_fallback_digest_contains_headline_and_links(self):
        digest = build_fallback_digest(_items(), max_items=2)
        assert "HEADLINE" in digest
        assert "[Link](https://a)" in digest
        assert "QUICK FALLBACK BRIEF" in digest

    def test_summarize_with_llm_returns_text(self):
        cfg = Config()
        llm = FakeLLM(
            responses=[
                LLMResponse(
                    text="LLM summary output",
                    tool_calls=[],
                    raw_content=[{"type": "text", "text": "LLM summary output"}],
                    input_tokens=1,
                    output_tokens=1,
                )
            ]
        )
        out = summarize_with_llm(llm, _items(), cfg)
        assert out == "LLM summary output"

    def test_summarize_with_llm_retries_and_falls_back_to_none(self, monkeypatch):
        cfg = Config()
        cfg.news.llm.retries = 2
        cfg.news.llm.retry_delay_sec = 0
        monkeypatch.setattr("archon.news.summarize.time.sleep", lambda _x: None)
        llm = FakeLLM(responses=[RuntimeError("boom"), RuntimeError("boom")])
        out = summarize_with_llm(llm, _items(), cfg)
        assert out is None

    def test_summarize_with_llm_records_news_usage_when_usage_present(self):
        cfg = Config()
        recorded = []
        llm = FakeLLM(
            responses=[
                LLMResponse(
                    text="LLM summary output",
                    tool_calls=[],
                    raw_content=[{"type": "text", "text": "LLM summary output"}],
                    input_tokens=12,
                    output_tokens=4,
                )
            ]
        )

        out = summarize_with_llm(
            llm,
            _items(),
            cfg,
            usage_recorder=lambda **kwargs: recorded.append(kwargs),
        )

        assert out == "LLM summary output"
        assert recorded == [
            {
                "source": "news",
                "provider": "google",
                "model": "gemini-3.1-pro-preview",
                "input_tokens": 12,
                "output_tokens": 4,
            }
        ]

    def test_summarize_with_llm_does_not_fabricate_usage_for_fallback(self, monkeypatch):
        cfg = Config()
        cfg.news.llm.retries = 2
        cfg.news.llm.retry_delay_sec = 0
        monkeypatch.setattr("archon.news.summarize.time.sleep", lambda _x: None)
        recorded = []
        llm = FakeLLM(responses=[RuntimeError("boom"), RuntimeError("boom")])

        out = summarize_with_llm(
            llm,
            _items(),
            cfg,
            usage_recorder=lambda **kwargs: recorded.append(kwargs),
        )

        assert out is None
        assert recorded == []
