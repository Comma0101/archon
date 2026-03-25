"""Shared non-streaming LLM retry and timeout helpers."""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from typing import TypeVar, cast

from archon.llm import LLMResponse


T = TypeVar("T")


def _is_transient_llm_error(exc: Exception) -> bool:
    text = str(exc).upper()
    transient_markers = (
        "503",
        "500",
        "502",
        "504",
        "429",
        "UNAVAILABLE",
        "RATE LIMIT",
        "TIMEOUT",
        "TEMPORAR",
        "TRY AGAIN",
    )
    return any(marker in text for marker in transient_markers)


def _chat_with_retry(
    llm,
    system_prompt: str,
    history: list[dict],
    tools: list[dict],
    max_attempts: int = 3,
    request_timeout_sec: float | None = None,
    is_transient_error: Callable[[Exception], bool] | None = None,
) -> LLMResponse:
    """Best-effort retry for transient provider errors."""
    delays = (0.35, 1.0)
    transient_predicate = is_transient_error or (lambda _exc: False)
    attempt = 0
    while True:
        attempt += 1
        try:
            return _call_with_timeout(
                lambda: llm.chat(system_prompt, history, tools=tools),
                request_timeout_sec,
            )
        except Exception as exc:
            if attempt >= max_attempts or not transient_predicate(exc):
                raise
            time.sleep(delays[min(attempt - 1, len(delays) - 1)])


def _call_with_timeout(fn: Callable[[], T], timeout_sec: float | None) -> T:
    if timeout_sec is None or float(timeout_sec) <= 0:
        return fn()

    mailbox: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

    def _runner() -> None:
        try:
            mailbox.put((True, fn()))
        except Exception as exc:
            mailbox.put((False, exc))

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    try:
        ok, payload = mailbox.get(timeout=float(timeout_sec))
    except queue.Empty as exc:
        raise TimeoutError(f"LLM request TIMEOUT after {timeout_sec}s") from exc
    if ok:
        return cast(T, payload)
    raise cast(Exception, payload)
