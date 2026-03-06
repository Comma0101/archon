"""Archon-native news runner (fetch -> filter -> summarize -> optional send)."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

from archon.adapters.telegram_client import TelegramBotClient
from archon.config import Config
from archon.llm import LLMClient
from archon.news.fetchers import fetch_all_sources
from archon.news.formatting import build_final_message, truncate_for_telegram
from archon.news.models import NewsDigest, NewsRunResult
from archon.news.pipeline import prefilter_items, select_digest_items
from archon.news.state import (
    load_cached_digest,
    load_news_state,
    save_cached_digest,
    save_news_state,
    should_run_today,
)
from archon.news.summarize import build_fallback_digest, summarize_with_llm


def run_news(
    config: Config,
    force: bool = False,
    send_telegram: bool = False,
    preview_only: bool = False,
    *,
    now: dt.datetime | None = None,
    state_path: Path | None = None,
    cache_path: Path | None = None,
    fetch_items_fn=None,
    summarize_fn=None,
    send_fn=None,
) -> NewsRunResult:
    """Run the Archon-native news pipeline."""

    now_dt = now or dt.datetime.now()
    date_iso = now_dt.date().isoformat()
    sender_requested = bool(send_telegram)
    state_file = state_path
    cache_file = cache_path
    if cache_file is None and state_file is not None:
        cache_file = Path(state_file).with_name(f"digest-{date_iso}.json")

    fetch_items_fn = fetch_items_fn or fetch_all_sources
    summarize_fn = summarize_fn or _summarize_items
    send_fn = send_fn or send_digest_to_telegram

    if preview_only:
        return get_or_build_news_digest(
            config,
            force_refresh=force,
            now=now_dt,
            cache_path=cache_file,
            fetch_items_fn=fetch_items_fn,
            summarize_fn=summarize_fn,
            result_status="preview",
        )

    if not config.news.enabled:
        return NewsRunResult(
            status="skipped",
            reason="news.disabled (set [news].enabled = true to enable scheduled runs)",
        )

    can_run, skip_reason = should_run_today(
        force=force,
        run_after_hour_local=config.news.schedule.run_after_hour_local,
        now=now_dt,
        state=load_news_state(state_path) if state_path is not None else None,
    )
    if not can_run:
        return NewsRunResult(status="skipped", reason=skip_reason or "skipped")

    build_result = get_or_build_news_digest(
        config,
        force_refresh=force,
        now=now_dt,
        cache_path=cache_file,
        fetch_items_fn=fetch_items_fn,
        summarize_fn=summarize_fn,
        result_status="built",
    )
    if build_result.status in ("error", "no_news"):
        if build_result.status == "no_news":
            save_news_state("success_no_news", path=state_file, now=now_dt)
        else:
            save_news_state("error", path=state_file, now=now_dt)
        return build_result

    if build_result.digest is None:
        save_news_state("error", path=state_file, now=now_dt)
        return NewsRunResult(status="error", reason="runner_missing_digest")

    if sender_requested and config.news.telegram.send_enabled:
        try:
            send_fn(config, build_result.digest.markdown)
        except Exception as e:
            save_news_state("error", path=state_file, now=now_dt)
            return NewsRunResult(
                status="error",
                reason=f"telegram_send_failed: {type(e).__name__}: {e}",
                digest=build_result.digest,
            )
        save_news_state(
            "fallback" if build_result.digest.used_fallback else "success",
            path=state_file,
            now=now_dt,
        )
        return NewsRunResult(status="sent", reason="", digest=build_result.digest)

    save_news_state(
        "built_fallback" if build_result.digest.used_fallback else "built",
        path=state_file,
        now=now_dt,
    )
    reason = "delivery_disabled"
    if sender_requested and not config.news.telegram.send_enabled:
        reason = "news.telegram.send_enabled=false"
    return NewsRunResult(status="built", reason=reason, digest=build_result.digest)


def get_or_build_news_digest(
    config: Config,
    force_refresh: bool = False,
    *,
    now: dt.datetime | None = None,
    cache_path: Path | None = None,
    fetch_items_fn=None,
    summarize_fn=None,
    result_status: str = "built",
) -> NewsRunResult:
    """Return today's cached digest or build a fresh one and cache it."""
    now_dt = now or dt.datetime.now()
    date_iso = now_dt.date().isoformat()
    fetch_items_fn = fetch_items_fn or fetch_all_sources
    summarize_fn = summarize_fn or _summarize_items

    if not force_refresh:
        cached = load_cached_digest(date_iso=date_iso, path=cache_path)
        if cached is not None:
            return NewsRunResult(
                status=result_status,
                reason="cache_hit",
                digest=cached,
            )

    result = _run_build_only_pipeline(
        config,
        date_iso=date_iso,
        fetch_items_fn=fetch_items_fn,
        summarize_fn=summarize_fn,
        status=result_status,
    )
    if result.digest is not None:
        save_cached_digest(result.digest, path=cache_path, now=now_dt)
    return result


def _run_build_only_pipeline(
    config: Config,
    *,
    date_iso: str,
    fetch_items_fn,
    summarize_fn,
    status: str,
) -> NewsRunResult:
    try:
        raw_items = fetch_items_fn(config)
    except Exception as e:
        return NewsRunResult(
            status="error",
            reason=f"fetch_failed: {type(e).__name__}: {e}",
        )
    if not raw_items:
        return NewsRunResult(status="no_news", reason="no_items_fetched")

    prefiltered = prefilter_items(raw_items, config)
    if not prefiltered:
        return NewsRunResult(status="no_news", reason="no_items_after_prefilter")

    digest_items = select_digest_items(prefiltered, config)
    if not digest_items:
        return NewsRunResult(status="no_news", reason="no_items_selected")

    try:
        digest_body, used_fallback = summarize_fn(config, digest_items)
    except Exception as e:
        return NewsRunResult(
            status="error",
            reason=f"summarize_failed: {type(e).__name__}: {e}",
        )
    if not digest_body:
        return NewsRunResult(status="error", reason="digest_generation_failed")

    final_msg = build_final_message(digest_body, date_iso)
    final_msg = truncate_for_telegram(final_msg, limit=4000)

    digest = NewsDigest(
        date_iso=date_iso,
        markdown=final_msg,
        used_fallback=used_fallback,
        item_count=len(digest_items),
        items=digest_items,
    )
    return NewsRunResult(status=status, reason="", digest=digest)


def _summarize_items(config: Config, items) -> tuple[str, bool]:
    """Summarize using Archon's LLMClient and fall back deterministically."""
    llm = None
    try:
        llm = _make_llm_client(config)
    except Exception as e:
        print(f"[news] LLM client init failed: {type(e).__name__}: {e}", file=sys.stderr)

    if llm is not None:
        text = summarize_with_llm(llm, items, config)
        if text:
            return text, False

    return build_fallback_digest(items, max_items=config.news.max_items), True


def _make_llm_client(config: Config) -> LLMClient:
    """Construct an LLM client from Archon's active config."""
    return LLMClient(
        provider=config.llm.provider,
        model=config.llm.model,
        api_key=config.llm.api_key,
        temperature=config.agent.temperature,
        base_url=config.llm.base_url,
    )


def _resolve_news_chat_ids(config: Config) -> list[int]:
    chat_ids = list(config.news.telegram.chat_ids)
    if not chat_ids:
        chat_ids = list(config.telegram.allowed_user_ids)
    return [int(x) for x in chat_ids]


def send_digest_to_telegram(config: Config, text: str) -> None:
    """Send digest text directly to configured Telegram chat IDs via Bot API."""
    token = (config.telegram.token or "").strip()
    if not token:
        raise ValueError("missing Telegram bot token (set [telegram].token or TELEGRAM_BOT_TOKEN)")
    chat_ids = _resolve_news_chat_ids(config)
    if not chat_ids:
        raise ValueError("missing news telegram chat ids (set [news.telegram].chat_ids or [telegram].allowed_user_ids)")

    bot = TelegramBotClient(token)
    for chat_id in chat_ids:
        bot.send_text(int(chat_id), text, timeout=15, limit=4000)
