"""Lightweight web search providers (SearxNG, Brave, DuckDuckGo HTML fallback)."""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from typing import Iterable
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    source: str = ""

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source": self.source,
        }


def search_web(
    query: str,
    *,
    config=None,
    limit: int | None = None,
    domains: list[str] | None = None,
    recency_days: int | None = None,
    fetch_text_fn=None,
    fetch_json_fn=None,
) -> tuple[list[SearchResult], dict]:
    """Search the web and return normalized results plus metadata."""
    q = (query or "").strip()
    if not q:
        return [], {"provider": "none", "notes": ["empty_query"]}

    provider = _resolve_provider(config)
    timeout = _timeout(config)
    ua = _user_agent(config)
    limit_n = max(1, min(int(limit or _default_limit(config)), 10))
    domains_norm = _normalize_domains(domains or [])
    notes: list[str] = []

    if recency_days is not None and recency_days < 0:
        recency_days = None

    fetch_text_fn = fetch_text_fn or _fetch_text
    fetch_json_fn = fetch_json_fn or _fetch_json

    providers = _provider_candidates(provider, config)
    used_provider = providers[-1] if providers else "duckduckgo_html"
    results: list[SearchResult] = []
    for current_provider in providers:
        try:
            batch = _search_with_provider(
                current_provider,
                q,
                config=config,
                limit=limit_n,
                recency_days=recency_days,
                fetch_text_fn=fetch_text_fn,
                fetch_json_fn=fetch_json_fn,
                timeout=timeout,
                user_agent=ua,
                notes=notes,
            )
        except Exception as exc:
            notes.append(f"{current_provider}_error:{type(exc).__name__}")
            continue
        if batch:
            used_provider = current_provider
            results = batch
            break
        notes.append(f"{current_provider}_no_results")

    if domains_norm:
        before = len(results)
        results = [r for r in results if _url_matches_domains(r.url, domains_norm)]
        if len(results) < before:
            notes.append(f"domain_filter_applied:{before}->{len(results)}")

    return results[:limit_n], {"provider": used_provider, "notes": notes}


def _resolve_provider(config) -> str:
    if config is None:
        return "duckduckgo_html"
    provider = str(getattr(config.web, "provider", "duckduckgo_html") or "duckduckgo_html").lower().strip()
    if provider not in {"auto", "duckduckgo_html", "searxng", "brave"}:
        return "duckduckgo_html"
    return provider


def _provider_candidates(provider: str, config) -> list[str]:
    if provider == "auto":
        out: list[str] = []
        if config is not None and str(getattr(config.web, "searxng_base_url", "") or "").strip():
            out.append("searxng")
        if config is not None and str(getattr(config.web, "brave_api_key", "") or "").strip():
            out.append("brave")
        out.append("duckduckgo_html")
        return out
    if provider == "searxng":
        return ["searxng", "duckduckgo_html"]
    if provider == "brave":
        return ["brave", "duckduckgo_html"]
    return ["duckduckgo_html"]


def _search_with_provider(
    provider: str,
    query: str,
    *,
    config,
    limit: int,
    recency_days: int | None,
    fetch_text_fn,
    fetch_json_fn,
    timeout: int,
    user_agent: str,
    notes: list[str],
) -> list[SearchResult]:
    if provider == "searxng":
        return _search_searxng(
            query,
            config=config,
            limit=limit,
            recency_days=recency_days,
            fetch_json_fn=fetch_json_fn,
            timeout=timeout,
            user_agent=user_agent,
        )
    if provider == "brave":
        return _search_brave(
            query,
            config=config,
            limit=limit,
            recency_days=recency_days,
            fetch_json_fn=fetch_json_fn,
            timeout=timeout,
            user_agent=user_agent,
        )
    if recency_days is not None:
        notes.append("recency_filter_requested_but_provider_may_ignore_it")
    return _search_duckduckgo_html(
        query,
        limit=limit,
        fetch_text_fn=fetch_text_fn,
        timeout=timeout,
        user_agent=user_agent,
    )


def _default_limit(config) -> int:
    if config is None:
        return 5
    return max(1, int(getattr(config.web, "max_results", 5)))


def _timeout(config) -> int:
    if config is None:
        return 15
    return max(1, int(getattr(config.web, "timeout_sec", 15)))


def _user_agent(config) -> str:
    if config is None:
        return "Archon-Web/0.1"
    return str(getattr(config.web, "user_agent", "Archon-Web/0.1") or "Archon-Web/0.1")


def _search_searxng(
    query: str,
    *,
    config,
    limit: int,
    recency_days: int | None,
    fetch_json_fn,
    timeout: int,
    user_agent: str,
) -> list[SearchResult]:
    base_url = str(getattr(config.web, "searxng_base_url", "") or "").rstrip("/")
    if not base_url:
        raise ValueError("web provider 'searxng' requires web.searxng_base_url")

    params = {
        "q": query,
        "format": "json",
        "safesearch": 1,
    }
    if recency_days is not None:
        if recency_days <= 1:
            params["time_range"] = "day"
        elif recency_days <= 31:
            params["time_range"] = "month"
        else:
            params["time_range"] = "year"
    url = f"{base_url}/search?{urlparse.urlencode(params)}"
    data = fetch_json_fn(url, headers={"User-Agent": user_agent}, timeout=timeout)
    if not isinstance(data, dict):
        return []

    out: list[SearchResult] = []
    for item in (data.get("results") or [])[: max(limit * 2, limit)]:
        if not isinstance(item, dict):
            continue
        title = _clean_ws(str(item.get("title") or ""))
        link = str(item.get("url") or "")
        content = _clean_ws(str(item.get("content") or ""))
        if not title or not link:
            continue
        out.append(SearchResult(
            title=title,
            url=link,
            snippet=content,
            source=str(item.get("engine") or "searxng"),
        ))
    return out


def _search_brave(
    query: str,
    *,
    config,
    limit: int,
    recency_days: int | None,
    fetch_json_fn,
    timeout: int,
    user_agent: str,
) -> list[SearchResult]:
    api_key = str(getattr(config.web, "brave_api_key", "") or "")
    if not api_key:
        raise ValueError("web provider 'brave' requires web.brave_api_key or BRAVE_SEARCH_API_KEY")

    params = {"q": query, "count": limit}
    if recency_days is not None:
        if recency_days <= 1:
            params["freshness"] = "pd"
        elif recency_days <= 7:
            params["freshness"] = "pw"
        elif recency_days <= 31:
            params["freshness"] = "pm"
        else:
            params["freshness"] = "py"

    url = f"https://api.search.brave.com/res/v1/web/search?{urlparse.urlencode(params)}"
    data = fetch_json_fn(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": user_agent,
            "X-Subscription-Token": api_key,
        },
        timeout=timeout,
    )
    if not isinstance(data, dict):
        return []

    out: list[SearchResult] = []
    web = data.get("web") or {}
    for item in (web.get("results") or [])[: max(limit * 2, limit)]:
        if not isinstance(item, dict):
            continue
        title = _clean_ws(str(item.get("title") or ""))
        link = str(item.get("url") or "")
        snippet = _clean_ws(str(item.get("description") or ""))
        if not title or not link:
            continue
        out.append(SearchResult(title=title, url=link, snippet=snippet, source="brave"))
    return out


def _search_duckduckgo_html(
    query: str,
    *,
    limit: int,
    fetch_text_fn,
    timeout: int,
    user_agent: str,
) -> list[SearchResult]:
    url = "https://html.duckduckgo.com/html/?" + urlparse.urlencode({"q": query})
    html_text = fetch_text_fn(url, headers={"User-Agent": user_agent}, timeout=timeout)
    if not html_text:
        return []
    return _parse_duckduckgo_html(html_text)[: max(limit * 2, limit)]


class _DdgHtmlParser:
    """Minimal regex parser for DDG HTML search results."""

    _link_re = re.compile(
        r'<a\b[^>]*class="[^"]*result__a[^"]*"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    _snippet_re = re.compile(
        r'<a\b[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(?P<snippet>.*?)</a>|'
        r'<div\b[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(?P<snippet_div>.*?)</div>',
        re.IGNORECASE | re.DOTALL,
    )
    _tag_re = re.compile(r"<[^>]+>")

    def parse(self, page_html: str) -> list[SearchResult]:
        results: list[SearchResult] = []
        snippets = [self._clean(m.group("snippet") or m.group("snippet_div") or "")
                    for m in self._snippet_re.finditer(page_html)]

        for idx, match in enumerate(self._link_re.finditer(page_html)):
            href = html.unescape(match.group("href"))
            title = self._clean(match.group("title"))
            if not title or not href:
                continue
            url = _unwrap_duckduckgo_redirect(href)
            snippet = snippets[idx] if idx < len(snippets) else ""
            results.append(SearchResult(title=title, url=url, snippet=snippet, source="duckduckgo"))
        return results

    def _clean(self, raw: str) -> str:
        text = html.unescape(self._tag_re.sub("", raw))
        return _clean_ws(text)


def _parse_duckduckgo_html(page_html: str) -> list[SearchResult]:
    return _DdgHtmlParser().parse(page_html)


def _unwrap_duckduckgo_redirect(url: str) -> str:
    parsed = urlparse.urlparse(url)
    if parsed.path.startswith("/l/"):
        qs = urlparse.parse_qs(parsed.query)
        uddg = qs.get("uddg")
        if uddg:
            return html.unescape(uddg[0])
    return url


def _normalize_domains(domains: Iterable[str]) -> list[str]:
    out: list[str] = []
    for raw in domains:
        d = str(raw or "").strip().lower()
        if not d:
            continue
        if "://" in d:
            d = (urlparse.urlparse(d).hostname or "").lower()
        if d.startswith("www."):
            d = d[4:]
        if d:
            out.append(d)
    return out


def _url_matches_domains(url: str, domains: list[str]) -> bool:
    try:
        host = (urlparse.urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if host.startswith("www."):
        host = host[4:]
    return any(host == d or host.endswith(f".{d}") for d in domains)


def _clean_ws(text: str) -> str:
    return " ".join((text or "").split())


def _fetch_text(url: str, *, headers: dict | None = None, timeout: int = 15) -> str | None:
    req = urlrequest.Request(url, headers=headers or {}, method="GET")
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace")
    except (urlerror.HTTPError, urlerror.URLError, TimeoutError):
        return None


def _fetch_json(url: str, *, headers: dict | None = None, timeout: int = 15):
    text = _fetch_text(url, headers=headers, timeout=timeout)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
