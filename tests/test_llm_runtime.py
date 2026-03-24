"""Tests for shared LLM runtime helpers."""

import threading
from unittest.mock import MagicMock

import pytest

from archon.execution.llm_runtime import _call_with_timeout, _chat_with_retry
from archon.llm import LLMResponse


def _make_response(text: str = "ok") -> LLMResponse:
    return LLMResponse(
        text=text,
        tool_calls=[],
        raw_content=[{"type": "text", "text": text}],
        input_tokens=3,
        output_tokens=1,
    )


def test_chat_with_retry_retries_transient_provider_error(monkeypatch):
    llm = MagicMock()
    llm.chat = MagicMock(side_effect=[RuntimeError("503 UNAVAILABLE"), _make_response("Recovered")])
    slept: list[float] = []
    monkeypatch.setattr("archon.execution.llm_runtime.time.sleep", lambda secs: slept.append(secs))

    result = _chat_with_retry(
        llm,
        "system",
        [],
        [],
        max_attempts=3,
        request_timeout_sec=1.0,
        is_transient_error=lambda exc: "503" in str(exc),
    )

    assert result.text == "Recovered"
    assert llm.chat.call_count == 2
    assert slept


def test_chat_with_retry_does_not_retry_non_transient_failure(monkeypatch):
    llm = MagicMock()
    llm.chat = MagicMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr("archon.execution.llm_runtime.time.sleep", lambda _secs: None)

    with pytest.raises(RuntimeError, match="boom"):
        _chat_with_retry(
            llm,
            "system",
            [],
            [],
            max_attempts=3,
            request_timeout_sec=1.0,
            is_transient_error=lambda _exc: False,
        )

    assert llm.chat.call_count == 1


def test_call_with_timeout_enforces_timeout():
    gate = threading.Event()

    def _slow_call():
        gate.wait(0.1)
        return _make_response("late")

    with pytest.raises(TimeoutError, match="TIMEOUT"):
        _call_with_timeout(_slow_call, 0.01)


def test_chat_with_retry_times_out_without_retrying_forever():
    llm = MagicMock()

    def _slow_chat(*_args, **_kwargs):
        gate = threading.Event()
        gate.wait(0.1)
        return _make_response("late")

    llm.chat = MagicMock(side_effect=_slow_chat)

    with pytest.raises(TimeoutError, match="TIMEOUT"):
        _chat_with_retry(
            llm,
            "system",
            [],
            [],
            max_attempts=1,
            request_timeout_sec=0.01,
            is_transient_error=lambda exc: "503" in str(exc),
        )

    assert llm.chat.call_count == 1
