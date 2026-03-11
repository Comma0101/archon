"""LLM summarization layer for news digests with deterministic fallback."""

from __future__ import annotations

import time

from typing import Callable

from archon.news.models import NewsItem


def build_news_prompt(items: list[NewsItem]) -> str:
    """Build a structured prompt payload listing ranked items."""
    lines = []
    for i, item in enumerate(items, start=1):
        lines.append(
            f"{i}. [{item.source}] {item.title} ({item.url}) - Score: {item.score:g}"
        )

    item_list = "\n".join(lines) if lines else "(no items)"
    return (
        "You are an Expert AI News Analyst.\n"
        "You are given a ranked list of raw AI/ML news items from HN, GitHub, "
        "Hugging Face, and Reddit.\n\n"
        "Task:\n"
        "1. Pick the single most important headline.\n"
        "2. Select the most relevant 10-15 items.\n"
        "3. Deduplicate near-duplicates across sources.\n"
        "4. Group into sections:\n"
        "   - 🚀 MAJOR RELEASES\n"
        "   - 🛠️ THE BUILDER'S STACK\n"
        "   - 🧠 RESEARCH & THEORY\n"
        "   - 🔥 THE WATERCOOLER\n"
        "5. Write one concise line per item.\n\n"
        "Formatting requirements (Telegram-safe markdown):\n"
        "- Do not combine bold and links.\n"
        "- Use this headline pattern:\n"
        "  🏆 **HEADLINE**: Title\n"
        "  [Link](url)\n"
        "  Two-sentence summary.\n"
        "- Use section headers like:\n"
        "  ➖➖➖➖➖➖\n"
        "  **🚀 MAJOR RELEASES**\n"
        "- Use item bullets like:\n"
        "  * 🔹 **Title**: Summary. [Link](url) (Source · Score)\n\n"
        "Raw ranked items:\n"
        f"{item_list}\n"
    )


def summarize_with_llm(
    llm,
    items: list[NewsItem],
    config,
    *,
    usage_recorder: Callable[..., None] | None = None,
) -> str | None:
    """Summarize ranked items using the configured LLM, retrying on failure."""
    if not items:
        return None

    attempts = max(1, int(config.news.llm.retries))
    retry_delay = float(config.news.llm.retry_delay_sec)
    system_prompt = (
        "You write high-signal AI news briefings for Telegram. "
        "Be concise, accurate, and preserve markdown formatting requirements."
    )
    user_prompt = build_news_prompt(items)

    for attempt in range(1, attempts + 1):
        try:
            response = llm.chat(
                system_prompt,
                [{"role": "user", "content": user_prompt}],
                tools=None,
            )
        except Exception:
            if attempt < attempts:
                time.sleep(retry_delay * attempt)
                continue
            return None

        text = (response.text or "").strip()
        if response.tool_calls:
            text = ""
        if text and "LLM error:" in text and "\"status\": \"UNAVAILABLE\"" in text:
            text = ""

        if text:
            if callable(usage_recorder):
                input_tokens = getattr(response, "input_tokens", None)
                output_tokens = getattr(response, "output_tokens", None)
                if input_tokens is not None and output_tokens is not None:
                    usage_recorder(
                        source="news",
                        provider=str(getattr(llm, "provider", "") or "").strip(),
                        model=str(getattr(llm, "model", "") or "").strip(),
                        input_tokens=int(input_tokens),
                        output_tokens=int(output_tokens),
                    )
            return text

        if attempt < attempts:
            time.sleep(retry_delay * attempt)

    return None


def build_fallback_digest(items: list[NewsItem], max_items: int = 12) -> str:
    """Deterministic digest for provider outages or local preview."""
    if not items:
        return "No high-signal items found."

    ranked = sorted(items, key=lambda x: float(x.score), reverse=True)[:max_items]
    top = ranked[0]
    lines = [
        f"🏆 **HEADLINE**: {top.title}",
        f"[Link]({top.url})",
        "LLM summary was unavailable, so this is a deterministic fallback digest from ranked source data.",
        "",
        "➖➖➖➖➖➖",
        "**🛠️ QUICK FALLBACK BRIEF**",
    ]

    for item in ranked:
        lines.append(
            f"* 🔹 **{item.title}**: [Link]({item.url}) ({item.source} · {item.score:g})"
        )

    return "\n".join(lines)
