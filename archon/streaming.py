"""Shared helpers for incremental final-text streaming."""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from collections.abc import Callable, Generator, Iterable

from archon.llm import LLMResponse


_STREAM_END = object()


@dataclass
class StreamPumpResult:
    response: LLMResponse | None
    emitted_any_text: bool


def stream_chat_with_retry(
    *,
    llm,
    system_prompt: str,
    history: list[dict],
    tools: list[dict],
    on_text_delta: Callable[[str], None],
    on_fallback_chat: Callable[[], LLMResponse] | None = None,
    is_transient_error: Callable[[Exception], bool] | None = None,
    max_attempts: int = 3,
    request_timeout_sec: float | None = None,
    retry_delays: Iterable[float] = (0.35, 1.0),
) -> StreamPumpResult:
    """Push streaming deltas immediately, with best-effort pre-delta fallback.

    Retries happen only before any visible text has been yielded. If streaming
    still fails before the first text delta, a one-shot buffered fallback can be
    used to preserve user-visible behavior.
    """
    response: LLMResponse | None = None
    emitted_any_text = False
    for chunk in _iter_stream_chat_with_retry(
        llm=llm,
        system_prompt=system_prompt,
        history=history,
        tools=tools,
        on_fallback_chat=on_fallback_chat,
        is_transient_error=is_transient_error,
        max_attempts=max_attempts,
        request_timeout_sec=request_timeout_sec,
        retry_delays=retry_delays,
    ):
        if isinstance(chunk, str):
            emitted_any_text = True
            on_text_delta(chunk)
        else:
            response = chunk
    return StreamPumpResult(response=response, emitted_any_text=emitted_any_text)


def _iter_stream_chat_with_retry(
    *,
    llm,
    system_prompt: str,
    history: list[dict],
    tools: list[dict],
    on_fallback_chat: Callable[[], LLMResponse] | None = None,
    is_transient_error: Callable[[Exception], bool] | None = None,
    max_attempts: int = 3,
    request_timeout_sec: float | None = None,
    retry_delays: Iterable[float] = (0.35, 1.0),
) -> Generator[str | LLMResponse, None, None]:
    """Internal stream event iterator used by the callback-based pump."""
    delays = tuple(float(delay) for delay in retry_delays)
    transient_predicate = is_transient_error or (lambda _exc: False)
    attempt = 0

    while True:
        attempt += 1
        emitted_text = False
        try:
            for chunk in _stream_chat_with_timeout(
                llm=llm,
                system_prompt=system_prompt,
                history=history,
                tools=tools,
                timeout_sec=request_timeout_sec,
            ):
                if isinstance(chunk, str):
                    emitted_text = True
                yield chunk
            return
        except Exception as exc:
            if emitted_text:
                raise
            if attempt < max_attempts and transient_predicate(exc):
                time.sleep(delays[min(attempt - 1, len(delays) - 1)])
                continue
            if on_fallback_chat is None:
                raise
            response = _call_with_timeout(on_fallback_chat, request_timeout_sec)
            if response.text is not None:
                yield response.text
            yield response
            return


def _stream_chat_with_timeout(
    *,
    llm,
    system_prompt: str,
    history: list[dict],
    tools: list[dict],
    timeout_sec: float | None,
) -> Generator[str | LLMResponse, None, None]:
    if timeout_sec is None or float(timeout_sec) <= 0:
        yield from llm.chat_stream(system_prompt, history, tools=tools)
        return

    mailbox: queue.Queue[tuple[bool, object]] = queue.Queue()

    def _runner() -> None:
        try:
            for chunk in llm.chat_stream(system_prompt, history, tools=tools):
                mailbox.put((True, chunk))
            mailbox.put((True, _STREAM_END))
        except Exception as exc:
            mailbox.put((False, exc))

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    deadline = time.monotonic() + float(timeout_sec)

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"LLM request TIMEOUT after {timeout_sec}s")
        try:
            ok, payload = mailbox.get(timeout=remaining)
        except queue.Empty as exc:
            raise TimeoutError(f"LLM request TIMEOUT after {timeout_sec}s") from exc
        if not ok:
            raise payload  # type: ignore[misc]
        if payload is _STREAM_END:
            return
        yield payload  # type: ignore[misc]


def _call_with_timeout(fn: Callable[[], LLMResponse], timeout_sec: float | None) -> LLMResponse:
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
        return payload  # type: ignore[return-value]
    raise payload  # type: ignore[misc]
