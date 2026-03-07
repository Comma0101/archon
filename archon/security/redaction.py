"""Conservative secret-like text redaction helpers."""

from __future__ import annotations

import re


_SECRET_ASSIGNMENT_RE = re.compile(
    r"""(?imx)
    \b(?P<key>[A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*|api_key)\b
    (?P<closing_quote>["']?)
    (?P<sep>\s*[:=]\s*)
    (?P<value>"[^"\n]*"|'[^'\n]*'|[^\s,\}\]\n]+)
    """,
)
_ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_READLINE_MARKER_RE = re.compile(r"[\x01\x02]")
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def redact_secret_like_text(text: str) -> str:
    """Redact obvious secret assignments while preserving surrounding structure."""
    raw = str(text or "")
    if not raw:
        return ""

    def _replace(match: re.Match[str]) -> str:
        key = match.group("key")
        closing_quote = match.group("closing_quote")
        sep = match.group("sep")
        value = match.group("value")
        if value.startswith('"') and value.endswith('"'):
            return f'{key}{closing_quote}{sep}"[REDACTED]"'
        if value.startswith("'") and value.endswith("'"):
            return f"{key}{closing_quote}{sep}'[REDACTED]'"
        return f"{key}{closing_quote}{sep}[REDACTED]"

    return _SECRET_ASSIGNMENT_RE.sub(_replace, raw)


def sanitize_terminal_notice_text(text: str) -> str:
    """Redact obvious secrets and strip terminal control sequences for notices."""
    raw = redact_secret_like_text(text)
    raw = _ANSI_ESCAPE_RE.sub("", raw)
    raw = _READLINE_MARKER_RE.sub("", raw)
    raw = _CONTROL_CHAR_RE.sub(" ", raw)
    return " ".join(str(raw or "").split())


def strip_readline_prompt_markers(text: str) -> str:
    """Return a plain prompt string safe to redraw outside readline."""
    raw = str(text or "")
    raw = _READLINE_MARKER_RE.sub("", raw)
    raw = _ANSI_ESCAPE_RE.sub("", raw)
    raw = _CONTROL_CHAR_RE.sub("", raw)
    return raw
