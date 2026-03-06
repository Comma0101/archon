"""Web page fetch + extraction helpers for `web_read` tool."""

from __future__ import annotations

import html
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest


@dataclass
class WebPage:
    url: str
    final_url: str
    title: str
    content_type: str
    text: str

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "final_url": self.final_url,
            "title": self.title,
            "content_type": self.content_type,
            "text": self.text,
        }


def read_web_url(
    url: str,
    *,
    config=None,
    max_chars: int = 6000,
    fetch_fn=None,
) -> WebPage:
    """Fetch a URL and return extracted text content."""
    u = _validate_web_url(url)
    timeout = max(1, int(getattr(getattr(config, "web", object()), "timeout_sec", 15)))
    user_agent = str(getattr(getattr(config, "web", object()), "user_agent", "Archon-Web/0.1"))
    fetch_fn = fetch_fn or _fetch_url

    payload = fetch_fn(u, timeout=timeout, user_agent=user_agent)
    raw = payload["body"]
    content_type = str(payload.get("content_type") or "").lower()
    final_url = str(payload.get("final_url") or u)
    charset = str(payload.get("charset") or "utf-8")

    text = ""
    title = ""
    if "html" in content_type:
        decoded = raw.decode(charset, errors="replace")
        title, text = _extract_html_text(decoded)
    else:
        text = raw.decode(charset, errors="replace")
        if "json" in content_type:
            text = text.strip()

    text = _clean_ws(text)
    if max_chars > 0 and len(text) > max_chars:
        text = text[:max_chars] + f"\n... (truncated, {len(text) - max_chars} chars omitted)"

    return WebPage(
        url=u,
        final_url=final_url,
        title=title,
        content_type=content_type or "unknown",
        text=text or "(no readable text found)",
    )


def _validate_web_url(url: str) -> str:
    candidate = (url or "").strip()
    parsed = urlparse.urlparse(candidate)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("web_read only supports http:// and https:// URLs")
    host = (parsed.hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
        raise ValueError("web_read blocks localhost URLs; use shell for local services")
    return candidate


def _fetch_url(url: str, *, timeout: int = 15, user_agent: str = "Archon-Web/0.1") -> dict:
    req = urlrequest.Request(url, headers={"User-Agent": user_agent}, method="GET")
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return {
                "body": body,
                "final_url": resp.geturl(),
                "content_type": resp.headers.get("Content-Type", ""),
                "charset": resp.headers.get_content_charset() or "utf-8",
            }
    except urlerror.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}") from e
    except urlerror.URLError as e:
        raise RuntimeError(f"Network error: {e.reason}") from e


class _VisibleTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self._title_parts: list[str] = []
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        if tag in {"p", "div", "section", "article", "br", "li", "h1", "h2", "h3", "h4"}:
            self._text_parts.append("\n")

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if tag == "title":
            self._in_title = False
        if tag in {"p", "div", "section", "article", "li"}:
            self._text_parts.append("\n")

    def handle_data(self, data: str):
        if self._skip_depth > 0:
            return
        if not data or not data.strip():
            return
        if self._in_title:
            self._title_parts.append(data)
        self._text_parts.append(data)

    @property
    def title(self) -> str:
        return _clean_ws("".join(self._title_parts))

    @property
    def text(self) -> str:
        return _clean_ws(" ".join(self._text_parts))


def _extract_html_text(page_html: str) -> tuple[str, str]:
    parser = _VisibleTextExtractor()
    parser.feed(page_html)
    parser.close()
    text = parser.text

    if not text:
        # Last-resort tag strip for malformed HTML
        stripped = html.unescape(page_html)
        stripped = _clean_ws(stripped)
        text = stripped

    return parser.title, text


def _clean_ws(text: str) -> str:
    return " ".join((text or "").split())
