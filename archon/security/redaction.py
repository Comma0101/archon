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
