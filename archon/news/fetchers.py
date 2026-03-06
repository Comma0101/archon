"""News source fetchers using stdlib HTTP clients."""

from __future__ import annotations

import datetime as dt
import html
import json
import re
import sys
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from archon.news.models import NewsItem


DEFAULT_KEYWORDS = [
    "LLM", "GPT", "Transformer", "Diffusion", "Generative", "RAG", "Agent",
    "OpenAI", "Anthropic", "Gemini", "Llama", "Mistral", "NVIDIA", "CUDA",
    "Machine Learning", "Neural", "Embedding", "Vector", "Search", "DeepSeek",
    "Qwen", "LoRA", "Quantization", "Inference",
]

DEFAULT_BLOCKLIST = [
    "Top 10", "Best Tools", "How to make money", "Crypto", "Blockchain", "NFT",
    "Course", "FBI", "Police", "Arrest", "Crime", "Estate Agent",
    "Insurance Agent", "Travel Agent",
]


def fetch_all_sources(config) -> list[NewsItem]:
    """Fetch all enabled sources, returning a flat list."""
    items: list[NewsItem] = []
    sources = config.news.sources

    if sources.hacker_news:
        items.extend(_safe_fetch("hn", lambda: fetch_hn(config)))
    if sources.github:
        items.extend(_safe_fetch("github", lambda: fetch_github_trending(config)))
    if sources.huggingface:
        items.extend(_safe_fetch("huggingface", lambda: fetch_huggingface_papers(config)))
    if sources.reddit_localllama:
        items.extend(_safe_fetch("reddit_localllama", lambda: fetch_reddit_localllama(config)))

    return items


def fetch_hn(config=None, limit: int = 60) -> list[NewsItem]:
    """Fetch Hacker News top stories and apply keyword/blocklist filtering."""
    top_ids = _get_json("https://hacker-news.firebaseio.com/v0/topstories.json", timeout=10)
    if not isinstance(top_ids, list):
        return []

    keywords = _keywords(config)
    blocklist = _blocklist(config)
    results: list[NewsItem] = []

    for story_id in top_ids[:limit]:
        item = _get_json(
            f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json",
            timeout=5,
        )
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        if not _matches_keywords(title, keywords):
            continue
        if _matches_blocklist(title, blocklist):
            continue
        url = item.get("url") or f"https://news.ycombinator.com/item?id={story_id}"
        score = float(item.get("score", 0) or 0)
        results.append(NewsItem(source="HN", title=title, url=str(url), score=score))
    return results


def fetch_github_trending(config=None, per_page: int = 10) -> list[NewsItem]:
    """Fetch AI-related GitHub repositories from the search API."""
    _ = config
    date_str = (dt.date.today() - dt.timedelta(days=7)).isoformat()
    query = f"topic:ai created:>{date_str}"
    query_encoded = urlparse.quote_plus(query)
    url = (
        "https://api.github.com/search/repositories"
        f"?q={query_encoded}&sort=stars&order=desc&per_page={per_page}"
    )
    data = _get_json(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Archon-News/0.1",
        },
        timeout=10,
    )
    if not isinstance(data, dict):
        return []

    repos: list[NewsItem] = []
    for item in data.get("items", []) or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        desc = str(item.get("description") or "No description").strip()
        title = f"{name}: {desc}"
        repos.append(
            NewsItem(
                source="GitHub",
                title=title,
                url=str(item.get("html_url") or ""),
                score=float(item.get("stargazers_count", 0) or 0),
            )
        )
    return [r for r in repos if r.url]


def fetch_huggingface_papers(config=None, limit: int = 8) -> list[NewsItem]:
    """Scrape Hugging Face papers page for top visible papers (best-effort)."""
    _ = config
    html_text = _get_text(
        "https://huggingface.co/papers",
        headers={"User-Agent": "Archon-News/0.1"},
        timeout=10,
    )
    if not html_text:
        return []

    items = _parse_hf_papers_articles(html_text, limit=limit)
    return items


def fetch_reddit_localllama(config=None, limit: int = 15) -> list[NewsItem]:
    """Fetch top daily posts from r/LocalLLaMA."""
    _ = config
    url = f"https://www.reddit.com/r/LocalLLaMA/top.json?t=day&limit={limit}"
    data = _get_json(
        url,
        headers={"User-Agent": "Archon-News/0.1"},
        timeout=10,
    )
    if not isinstance(data, dict):
        return []

    posts: list[NewsItem] = []
    children = (((data.get("data") or {}).get("children")) or [])
    for child in children:
        post = (child or {}).get("data") or {}
        if not isinstance(post, dict):
            continue
        title = str(post.get("title", "")).strip()
        permalink = str(post.get("permalink") or "")
        if not title or not permalink:
            continue
        posts.append(
            NewsItem(
                source="Reddit",
                title=title,
                url=f"https://reddit.com{permalink}",
                score=float(post.get("score", 0) or 0),
            )
        )
    return posts


def _safe_fetch(name: str, fn) -> list[NewsItem]:
    try:
        return fn()
    except Exception as e:
        print(f"[news] {name} fetch failed: {type(e).__name__}: {e}", file=sys.stderr)
        return []


def _get_json(url: str, headers: dict | None = None, timeout: int = 10):
    text = _get_text(url, headers=headers, timeout=timeout)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _get_text(url: str, headers: dict | None = None, timeout: int = 10) -> str | None:
    req = urlrequest.Request(url, headers=headers or {}, method="GET")
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace")
    except (urlerror.HTTPError, urlerror.URLError, TimeoutError):
        return None


def _keywords(config) -> list[str]:
    if config and config.news.keywords:
        return [str(x) for x in config.news.keywords]
    return DEFAULT_KEYWORDS


def _blocklist(config) -> list[str]:
    if config and config.news.blocklist:
        return [str(x) for x in config.news.blocklist]
    return DEFAULT_BLOCKLIST


def _matches_keywords(text: str, keywords: list[str]) -> bool:
    lowered = text.lower()
    return any(k.lower() in lowered for k in keywords)


def _matches_blocklist(text: str, blocklist: list[str]) -> bool:
    lowered = text.lower()
    return any(b.lower() in lowered for b in blocklist)


def _parse_hf_papers_articles(page_html: str, limit: int = 8) -> list[NewsItem]:
    items: list[NewsItem] = []
    article_pattern = re.compile(r"<article\b.*?</article>", re.IGNORECASE | re.DOTALL)
    href_pattern = re.compile(r'href="(?P<href>/papers/[^"]+)"', re.IGNORECASE)
    h3_pattern = re.compile(r"<h3\b[^>]*>(?P<title>.*?)</h3>", re.IGNORECASE | re.DOTALL)
    tag_pattern = re.compile(r"<[^>]+>")

    for article in article_pattern.findall(page_html):
        href_match = href_pattern.search(article)
        title_match = h3_pattern.search(article)
        if not href_match or not title_match:
            continue
        href = href_match.group("href")
        title_html = title_match.group("title")
        title = html.unescape(tag_pattern.sub("", title_html)).strip()
        if not title:
            continue
        items.append(
            NewsItem(
                source="HF",
                title=f"Paper: {title}",
                url=f"https://huggingface.co{href}",
                score=100.0,
            )
        )
        if len(items) >= limit:
            break

    # Fallback if page structure changes and article tags disappear.
    if items:
        return items

    link_pattern = re.compile(
        r'<a\b[^>]*href="(?P<href>/papers/[^"]+)"[^>]*>(?P<label>.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    tag_pattern = re.compile(r"<[^>]+>")
    seen: set[str] = set()
    for match in link_pattern.finditer(page_html):
        href = match.group("href")
        label = html.unescape(tag_pattern.sub("", match.group("label"))).strip()
        if not label or href in seen:
            continue
        seen.add(href)
        items.append(
            NewsItem(
                source="HF",
                title=f"Paper: {label}",
                url=f"https://huggingface.co{href}",
                score=100.0,
            )
        )
        if len(items) >= limit:
            break
    return items
