"""Read/write/search persistent markdown memory files with a lightweight index."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import uuid

from archon.config import MEMORY_DIR


INDEX_FILENAME = "memory_index.json"
INBOX_FILENAME = "memory_inbox.jsonl"
AUTO_INDEX_START = "<!-- archon:auto-index:start -->"
AUTO_INDEX_END = "<!-- archon:auto-index:end -->"


def ensure_dir():
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def index_path() -> Path:
    ensure_dir()
    return MEMORY_DIR / INDEX_FILENAME


def inbox_path() -> Path:
    ensure_dir()
    return MEMORY_DIR / INBOX_FILENAME


def canonical_path(path: str) -> str:
    """Normalize memory paths and map known aliases to canonical locations."""
    raw = str(path or "").strip().replace("\\", "/").lstrip("/")
    if raw in {"system-profile.md", "profiles/system-profile.md"}:
        return "profiles/system.md"
    return raw


def read(path: str = "") -> str:
    """Read a memory file or list all files."""
    ensure_dir()
    if path:
        requested = str(path).strip().replace("\\", "/").lstrip("/")
        target = MEMORY_DIR / canonical_path(requested)
        if target.exists():
            return target.read_text()
        # Backward compatibility: if a legacy path exists and no canonical file exists yet,
        # read the legacy file so old memory layouts remain accessible until rewritten.
        legacy_target = MEMORY_DIR / requested
        if legacy_target.exists():
            return legacy_target.read_text()
        return ""
    # List all
    files = sorted(MEMORY_DIR.rglob("*.md"))
    return "\n".join(str(f.relative_to(MEMORY_DIR)) for f in files)


def write(path: str, content: str) -> str:
    """Write content to a memory file and return the canonical relative path used."""
    ensure_dir()
    rel_path = canonical_path(path)
    target = MEMORY_DIR / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    _ensure_memory_md_pointer_block()
    try:
        rebuild_index()
    except Exception:
        # Memory writes should still succeed even if index refresh fails.
        pass
    return rel_path


def search(query: str) -> list[tuple[str, int, str]]:
    """Search all memory files for a query. Returns (file, line_num, line)."""
    ensure_dir()
    results = []
    try:
        proc = subprocess.run(
            ["grep", "-rn", "--include=*.md", query, str(MEMORY_DIR)],
            capture_output=True, text=True, timeout=5,
        )
        for line in proc.stdout.strip().splitlines():
            if not line:
                continue
            # Format: /path/to/file:linenum:content
            parts = line.split(":", 2)
            if len(parts) >= 3:
                filepath = str(Path(parts[0]).relative_to(MEMORY_DIR))
                linenum = int(parts[1])
                content = parts[2]
                results.append((filepath, linenum, content))
    except Exception:
        pass
    return results


def summary(max_lines: int = 50) -> str:
    """Return first N lines of MEMORY.md for prompt injection."""
    main = MEMORY_DIR / "MEMORY.md"
    if not main.exists():
        return ""
    lines = main.read_text().splitlines()[:max_lines]
    return "\n".join(lines)


def list_files() -> list[str]:
    """List all memory file paths relative to MEMORY_DIR."""
    ensure_dir()
    files = sorted(MEMORY_DIR.rglob("*.md"))
    return [str(f.relative_to(MEMORY_DIR)) for f in files]


def inbox_add(
    *,
    kind: str,
    scope: str,
    summary: str,
    source: str = "",
    confidence: str = "medium",
    target_path: str = "",
    content: str = "",
) -> dict:
    """Queue a memory candidate for human review."""
    ensure_dir()
    kind_value = (kind or "note").strip().lower()
    scope_value = (scope or "global").strip()
    summary_value = (summary or "").strip()
    source_value = (source or "").strip()
    confidence_value = (confidence or "medium").strip().lower()
    target_value = canonical_path(target_path) if target_path else ""

    entries = _load_inbox_entries()
    for existing in reversed(entries):
        if (
            str(existing.get("kind", "")) == kind_value
            and str(existing.get("scope", "")) == scope_value
            and str(existing.get("summary", "")) == summary_value
            and str(existing.get("target_path", "")) == target_value
            and str(existing.get("status", "")) in {"pending", "applied"}
        ):
            return existing

    now = _now_iso()
    item = {
        "id": str(uuid.uuid4()),
        "created_at": now,
        "updated_at": now,
        "status": "pending",
        "kind": kind_value,
        "scope": scope_value,
        "summary": summary_value,
        "source": source_value,
        "confidence": confidence_value,
        "target_path": target_value,
        "content": content or "",
        "decision": "",
        "decided_at": "",
        "applied_path": "",
    }
    entries.append(item)
    _write_inbox_entries(entries)
    return item


def inbox_list(status: str = "pending", limit: int = 50) -> list[dict]:
    """List inbox items, optionally filtering by status."""
    entries = _load_inbox_entries()
    status_value = (status or "pending").strip().lower()
    if status_value and status_value not in {"all", "*"}:
        entries = [e for e in entries if str(e.get("status", "")).lower() == status_value]
    entries.sort(key=lambda e: (str(e.get("created_at", "")), str(e.get("id", ""))), reverse=True)
    return entries[: max(1, int(limit))]


def inbox_decide(
    inbox_id: str,
    decision: str,
    target_path: str = "",
    apply_mode: str = "append",
    section_heading: str = "",
) -> dict | None:
    """Apply or reject a queued memory candidate."""
    entries = _load_inbox_entries()
    decision_value = (decision or "").strip().lower()
    if decision_value not in {"apply", "reject", "rejected", "applied"}:
        return None
    apply_mode_value = (apply_mode or "append").strip().lower()
    if apply_mode_value not in {"append", "replace_section"}:
        return None
    section_heading_value = _normalize_section_heading(section_heading)
    target: dict | None = None
    for entry in entries:
        if str(entry.get("id", "")) == str(inbox_id):
            target = entry
            break
    if target is None:
        return None
    current_status = str(target.get("status", "")).strip().lower()

    now = _now_iso()
    if decision_value.startswith("reject"):
        if current_status == "rejected":
            return target
        if current_status == "applied":
            return None
        target["status"] = "rejected"
        target["decision"] = "reject"
        target["updated_at"] = now
        target["decided_at"] = now
        _write_inbox_entries(entries)
        return target

    if current_status == "applied":
        return target
    if current_status == "rejected":
        return None

    chosen_path = canonical_path(target_path) if target_path else str(target.get("target_path", "")).strip()
    if not chosen_path:
        return None
    content = str(target.get("content", ""))
    if not content.strip():
        content = f"- {str(target.get('summary', '')).strip()}\n"
    if apply_mode_value == "append":
        applied_path = _append_memory_markdown(chosen_path, content)
    else:
        applied_path = _replace_memory_markdown_section(
            chosen_path,
            section_heading=section_heading_value or _first_markdown_heading(content),
            content=content,
        )
        if not applied_path:
            return None
    target["status"] = "applied"
    target["decision"] = "apply"
    target["updated_at"] = now
    target["decided_at"] = now
    target["applied_path"] = applied_path
    target["target_path"] = applied_path
    target["apply_mode"] = apply_mode_value
    if section_heading_value:
        target["section_heading"] = section_heading_value
    _write_inbox_entries(entries)
    return target


def _ensure_memory_md_pointer_block() -> None:
    """Ensure MEMORY.md contains a compact auto-managed pointer block."""
    ensure_dir()
    memory_md = MEMORY_DIR / "MEMORY.md"
    existing = memory_md.read_text() if memory_md.exists() else ""
    block = _auto_pointer_block()
    if AUTO_INDEX_START in existing and AUTO_INDEX_END in existing:
        start = existing.index(AUTO_INDEX_START)
        end = existing.index(AUTO_INDEX_END) + len(AUTO_INDEX_END)
        updated = existing[:start].rstrip() + "\n\n" + block
        tail = existing[end:].lstrip()
        if tail:
            updated += "\n\n" + tail
    else:
        updated = existing.rstrip()
        if updated:
            updated += "\n\n"
        updated += block
    if updated != existing:
        memory_md.write_text(updated.rstrip() + "\n")


def _auto_pointer_block() -> str:
    return (
        f"{AUTO_INDEX_START}\n"
        "## Memory Index\n"
        "- `projects.md` - Project registry and pointers\n"
        "- `profiles/system.md` - System hardware/profile baseline (stable facts)\n"
        f"{AUTO_INDEX_END}"
    )


def _append_memory_markdown(path: str, content: str) -> str:
    rel_path = canonical_path(path)
    target = MEMORY_DIR / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = target.read_text() if target.exists() else ""
    addition = content if content.endswith("\n") else content + "\n"
    if existing and not existing.endswith("\n"):
        existing += "\n"
    target.write_text(existing + addition)
    _ensure_memory_md_pointer_block()
    try:
        rebuild_index()
    except Exception:
        pass
    return rel_path


def _replace_memory_markdown_section(path: str, section_heading: str, content: str) -> str | None:
    """Replace a markdown section by heading line, preserving the rest of the file.

    If the target file does not exist, writes `content` as a new file. If the file exists
    but the section heading is not found, returns None (safer than appending duplicate
    sections by accident).
    """
    rel_path = canonical_path(path)
    target = MEMORY_DIR / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)

    replacement = content if content.endswith("\n") else (content + "\n")
    if not target.exists():
        target.write_text(replacement)
        _ensure_memory_md_pointer_block()
        try:
            rebuild_index()
        except Exception:
            pass
        return rel_path

    heading = _normalize_section_heading(section_heading)
    if not heading:
        return None

    existing = target.read_text()
    lines = existing.splitlines(keepends=True)
    start_idx = None
    for idx, line in enumerate(lines):
        if line.strip() == heading:
            start_idx = idx
            break
    if start_idx is None:
        return None

    match = re.match(r"^(#{1,6})\s+", heading)
    if not match:
        return None
    level = len(match.group(1))

    end_idx = len(lines)
    for idx in range(start_idx + 1, len(lines)):
        stripped = lines[idx].lstrip()
        heading_match = re.match(r"^(#{1,6})\s+", stripped)
        if heading_match and len(heading_match.group(1)) <= level:
            end_idx = idx
            break

    before = "".join(lines[:start_idx])
    after = "".join(lines[end_idx:])
    if after and not replacement.endswith("\n\n") and not after.startswith("\n"):
        replacement += "\n"
    updated = before + replacement + after
    target.write_text(updated)
    _ensure_memory_md_pointer_block()
    try:
        rebuild_index()
    except Exception:
        pass
    return rel_path


def _normalize_section_heading(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("#"):
        return text
    return f"## {text}"


def _first_markdown_heading(content: str) -> str:
    for line in str(content or "").splitlines():
        stripped = line.strip()
        if re.match(r"^#{1,6}\s+\S", stripped):
            return stripped
    return ""


def _load_inbox_entries() -> list[dict]:
    path = inbox_path()
    if not path.exists():
        return []
    items: list[dict] = []
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            data = json.loads(stripped)
        except Exception:
            continue
        if isinstance(data, dict):
            items.append(data)
    return items


def _write_inbox_entries(entries: list[dict]) -> None:
    path = inbox_path()
    lines = [json.dumps(entry, sort_keys=True) for entry in entries]
    path.write_text(("\n".join(lines) + "\n") if lines else "")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_index(rebuild_if_missing: bool = True) -> dict:
    """Load memory index payload, rebuilding if missing/invalid."""
    path = index_path()
    if path.exists():
        try:
            data = json.loads(path.read_text())
            entries = data.get("entries")
            if isinstance(entries, list):
                return data
        except Exception:
            pass
    if rebuild_if_missing:
        return rebuild_index()
    return {"version": 1, "generated_at": "", "entries": []}


def rebuild_index() -> dict:
    """Rebuild the machine-readable memory index from markdown files."""
    ensure_dir()
    entries = [_build_index_entry(path) for path in sorted(MEMORY_DIR.rglob("*.md"))]
    payload = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "entry_count": len(entries),
        "entries": entries,
    }
    index_path().write_text(json.dumps(payload, indent=2, sort_keys=True))
    return payload


def lookup(query: str, limit: int = 5) -> list[dict]:
    """Lookup relevant memory files using index metadata + lexical scoring."""
    q = (query or "").strip()
    if not q:
        return []
    payload = load_index(rebuild_if_missing=True)
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return []
    q_tokens = _tokens(q)
    scored: list[dict] = []
    for raw in entries:
        if not isinstance(raw, dict):
            continue
        score = _score_entry(raw, q, q_tokens)
        if score <= 0:
            continue
        item = dict(raw)
        item["score"] = score
        scored.append(item)
    scored.sort(
        key=lambda e: (
            -float(e.get("score", 0)),
            str(e.get("kind", "")) == "archive",
            str(e.get("path", "")),
        )
    )
    return scored[: max(1, int(limit))]


def prefetch_for_query(
    query: str,
    limit: int = 2,
    min_score: float = 6.0,
    max_lines_per_file: int = 24,
    max_chars_per_file: int = 1000,
) -> list[dict]:
    """Return compact memory snippets for a query using the index (best-effort)."""
    hits = lookup(query, limit=max(1, min(int(limit), 5)))
    prefetched: list[dict] = []
    for hit in hits:
        try:
            score = float(hit.get("score", 0))
        except Exception:
            score = 0.0
        if score < float(min_score):
            continue
        path = str(hit.get("path", "")).strip()
        if not path:
            continue
        excerpt = _read_excerpt(path, max_lines=max_lines_per_file, max_chars=max_chars_per_file)
        if not excerpt:
            continue
        prefetched.append(
            {
                "path": path,
                "kind": hit.get("kind", ""),
                "layer": hit.get("layer", ""),
                "scope": hit.get("scope", ""),
                "stability": hit.get("stability", ""),
                "last_modified": hit.get("last_modified", ""),
                "confidence": hit.get("confidence", ""),
                "score": score,
                "excerpt": excerpt,
            }
        )
    return prefetched


def _build_index_entry(path: Path) -> dict:
    rel = str(path.relative_to(MEMORY_DIR))
    text = path.read_text(errors="replace")
    title = _extract_title(text) or path.stem.replace("-", " ").replace("_", " ").title()
    summary_text = _extract_summary(text)
    kind, scope, stability, layer = _classify(rel)
    aliases = _aliases_from_path(rel, title)
    tags = _tags_for_entry(rel, kind, scope)
    return {
        "path": rel,
        "title": title,
        "summary": summary_text,
        "kind": kind,
        "layer": layer,
        "scope": scope,
        "stability": stability,
        "aliases": aliases,
        "tags": tags,
        "last_modified": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
        "confidence": "high",
    }


def _read_excerpt(path: str, max_lines: int = 24, max_chars: int = 1000) -> str:
    text = read(path)
    if not text:
        return ""
    lines = text.splitlines()[: max(1, int(max_lines))]
    excerpt = "\n".join(lines).strip()
    if len(excerpt) > max_chars:
        excerpt = excerpt[: max_chars - 3].rstrip() + "..."
    return excerpt


def _extract_title(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


def _extract_summary(text: str, max_lines: int = 2, max_chars: int = 220) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if stripped.startswith("```"):
            continue
        lines.append(stripped)
        if len(lines) >= max_lines:
            break
    summary_text = " ".join(lines)
    if len(summary_text) > max_chars:
        summary_text = summary_text[: max_chars - 3].rstrip() + "..."
    return summary_text


def _classify(rel_path: str) -> tuple[str, str, str, str]:
    rel = rel_path.replace("\\", "/")
    if rel == "MEMORY.md":
        return "memory_index_human", "global", "semi_stable", "user"
    if rel == "projects.md":
        return "project_registry", "global", "semi_stable", "project"
    if rel.startswith("projects/"):
        slug = Path(rel).stem
        return "project", f"project:{slug}", "semi_stable", "project"
    if rel.startswith("profiles/"):
        slug = Path(rel).stem
        if slug in {"system", "system-profile"}:
            return "system_profile", "global", "semi_stable", "machine"
        return "profile", "global", "semi_stable", "user"
    if rel.startswith("compactions/sessions/"):
        return "compaction_summary", "session", "volatile", "session"
    if rel.startswith("compactions/tasks/"):
        return "compaction_summary", "task", "volatile", "task"
    if rel.startswith("decisions/"):
        return "decision", "global", "stable", "user"
    if rel.startswith("archive/"):
        return "archive", "archive", "volatile", "task"
    return "note", "global", "semi_stable", "user"


def _aliases_from_path(rel_path: str, title: str) -> list[str]:
    aliases = {
        Path(rel_path).stem.lower(),
        rel_path.lower(),
        title.lower(),
    }
    stem = Path(rel_path).stem.replace("_", "-").lower()
    aliases.add(stem)
    if stem.startswith("202") and "-" in stem:
        # decisions with date prefix: include topic-only alias
        aliases.add("-".join(stem.split("-")[3:]))
    return sorted(a for a in aliases if a)


def _tags_for_entry(rel_path: str, kind: str, scope: str) -> list[str]:
    tags = {kind}
    rel = rel_path.lower()
    if scope.startswith("project:"):
        tags.update({"project", scope.split(":", 1)[1]})
    if kind == "system_profile":
        tags.update({"system", "hardware", "cpu", "gpu", "ram", "storage"})
    if "memory" in rel:
        tags.add("memory")
    return sorted(tags)


_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._/-]*", re.IGNORECASE)
_PREFERENCE_PATTERNS = [
    re.compile(r"\bi prefer\b", re.IGNORECASE),
    re.compile(r"\buse .+ by default\b", re.IGNORECASE),
    re.compile(r"\bplease use\b", re.IGNORECASE),
    re.compile(r"\bdon't use\b", re.IGNORECASE),
    re.compile(r"\bdo not use\b", re.IGNORECASE),
]


def _tokens(text: str) -> set[str]:
    return {tok.lower() for tok in _TOKEN_RE.findall(text or "")}


def _score_entry(entry: dict, raw_query: str, q_tokens: set[str]) -> float:
    if not q_tokens:
        return 0.0
    path = str(entry.get("path", "")).lower()
    title = str(entry.get("title", "")).lower()
    summary_text = str(entry.get("summary", "")).lower()
    kind = str(entry.get("kind", "")).lower()
    aliases = [str(a).lower() for a in entry.get("aliases", []) if isinstance(a, str)]
    tags = [str(t).lower() for t in entry.get("tags", []) if isinstance(t, str)]

    score = 0.0
    if raw_query.lower() in path:
        score += 10.0
    if raw_query.lower() in title:
        score += 8.0

    path_tokens = _tokens(path)
    title_tokens = _tokens(title)
    summary_tokens = _tokens(summary_text)
    alias_tokens = set().union(*(_tokens(a) for a in aliases)) if aliases else set()
    tag_tokens = set(tags)

    for tok in q_tokens:
        if tok in path_tokens:
            score += 4.0
        if tok in title_tokens:
            score += 3.0
        if tok in alias_tokens:
            score += 2.5
        if tok in tag_tokens:
            score += 2.0
        if tok in summary_tokens:
            score += 1.5

    if kind == "archive":
        score -= 3.0
    if kind == "compaction_summary":
        score += 1.0
    return score


def compact_history(
    messages: list[dict],
    *,
    layer: str = "session",
    summary_id: str = "latest",
    max_entries: int = 8,
) -> dict:
    """Persist a compact markdown summary of prior conversation state."""
    layer_value = (layer or "session").strip().lower()
    if layer_value == "task":
        rel_path = f"compactions/tasks/{summary_id}.md"
        title = "# Task Compaction Summary"
    else:
        layer_value = "session"
        rel_path = f"compactions/sessions/{summary_id}.md"
        title = "# Session Compaction Summary"

    selected = list(messages or [])[-max(1, int(max_entries)) :]
    bullets: list[str] = []
    for message in selected:
        role = str(message.get("role", "unknown") or "unknown").strip()
        text = _flatten_message_content(message.get("content"))
        if not text:
            continue
        bullets.append(f"- {role}: {text}")

    if not bullets:
        bullets.append("- assistant: No compactable history content.")

    content = title + "\n\n" + "\n".join(bullets) + "\n"
    path = write(rel_path, content)
    summary = bullets[0][2:] if bullets else ""
    return {
        "path": path,
        "layer": layer_value,
        "summary": summary,
    }


def _flatten_message_content(content: object, max_chars: int = 240) -> str:
    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type", "")).strip()
            if item_type == "text":
                piece = str(item.get("text", "")).strip()
            elif item_type == "tool_result":
                piece = str(item.get("content", "")).strip()
            else:
                piece = ""
            if piece:
                parts.append(piece)
        text = " ".join(parts).strip()
    else:
        text = str(content or "").strip()

    if len(text) > max_chars:
        text = text[: max_chars - 3].rstrip() + "..."
    return text


def capture_preference_to_inbox(text: str, source: str = "user_message") -> dict | None:
    """Detect explicit user preference statements and queue them in the memory inbox."""
    raw = (text or "").strip()
    if not raw:
        return None
    if len(raw) > 400:
        return None
    if "?" in raw:
        return None
    lowered = raw.lower()
    if not any(p.search(raw) for p in _PREFERENCE_PATTERNS):
        return None
    if lowered.startswith(("what ", "how ", "why ", "when ", "where ")):
        return None
    summary = raw
    if len(summary) > 240:
        summary = summary[:237].rstrip() + "..."
    return inbox_add(
        kind="preference",
        scope="global",
        summary=summary,
        source=source,
        confidence="medium",
    )
