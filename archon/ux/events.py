"""Shared lightweight activity event payloads for assistant UX surfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ActivityEvent:
    """Compact activity notice that can be rendered across UX surfaces."""

    source: str
    message: str

    def render_text(self) -> str:
        source = (self.source or "activity").strip() or "activity"
        message = (self.message or "").strip() or "(empty)"
        return f"[{source}] {message}"


# ---------------------------------------------------------------------------
# Structured UX events for cross-surface activity feeds
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UXEvent:
    """Typed event that any UX surface (terminal, Telegram, web) can render."""

    kind: str  # tool_start, tool_end, iteration_progress, compaction_triggered, job_progress, job_completed
    session_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def render_text(self) -> str:
        """Best-effort single-line text for terminal/Telegram display."""
        k = self.kind
        d = self.data
        if k == "tool_start":
            name = d.get("name", "?")
            args = d.get("args", "")
            return f"[tool] {name}" + (f" {args}" if args else "")
        if k == "tool_end":
            name = d.get("name", "?")
            result = d.get("result", "")
            return f"[tool] {name} done" + (f": {result}" if result else "")
        if k == "tool_running":
            tool = d.get("tool", "?")
            detail_type = d.get("detail_type", "progress")
            if detail_type == "output_line":
                return f"[tool] {tool} | {d.get('line', '')}".rstrip()
            if detail_type == "heartbeat":
                elapsed_s = d.get("elapsed_s")
                if isinstance(elapsed_s, int | float):
                    return f"[tool] {tool} running ({elapsed_s:.1f}s)"
            return f"[tool] {tool} running"
        if k == "tool_blocked":
            tool = d.get("tool", "?")
            preview = d.get("command_preview", "")
            level = d.get("safety_level", "")
            text = f"[tool] {tool} blocked"
            if level:
                text += f" ({level})"
            if preview:
                text += f": {preview}"
            return text
        if k == "tool_diff":
            tool = d.get("tool", "?")
            path = d.get("path", "")
            return f"[tool] {tool} diff" + (f": {path}" if path else "")
        if k == "iteration_progress":
            return f"[progress] iteration {d.get('current', '?')}/{d.get('max', '?')}"
        if k == "compaction_triggered":
            return f"[compact] {d.get('before', '?')} -> {d.get('after', '?')} messages"
        if k == "job_progress":
            job_kind = d.get("job_kind", "job")
            job_id = d.get("job_id", "?")
            status = str(d.get("status", "running") or "running").replace("_", " ")
            summary = d.get("summary", "")
            text = f"[{job_kind}] {job_id} {status}"
            if summary:
                text += f": {summary}"
            return text
        if k == "job_completed":
            job_kind = d.get("job_kind", "job")
            job_id = d.get("job_id", "?")
            status = d.get("status", "done")
            summary = d.get("summary", "")
            text = f"[{job_kind}] {job_id} {status}"
            if summary:
                text += f": {summary}"
            return text
        return f"[{k}] {d}"


# ---------------------------------------------------------------------------
# Convenience constructors
# ---------------------------------------------------------------------------


def tool_start(name: str, args_summary: str = "", *, session_id: str = "") -> UXEvent:
    return UXEvent(
        kind="tool_start",
        session_id=session_id,
        data={"name": name, "args": args_summary, "session_id": session_id},
    )


def tool_end(name: str, result_summary: str = "", *, session_id: str = "") -> UXEvent:
    return UXEvent(
        kind="tool_end",
        session_id=session_id,
        data={"name": name, "result": result_summary, "session_id": session_id},
    )


def tool_running(
    *,
    tool: str,
    detail_type: str,
    session_id: str = "",
    line: str = "",
    elapsed_s: float | None = None,
) -> UXEvent:
    data: dict[str, Any] = {
        "tool": tool,
        "detail_type": detail_type,
        "session_id": session_id,
    }
    if line:
        data["line"] = line
    if elapsed_s is not None:
        data["elapsed_s"] = elapsed_s
    return UXEvent(kind="tool_running", session_id=session_id, data=data)


def tool_blocked(
    *,
    tool: str,
    command_preview: str,
    safety_level: str,
    session_id: str = "",
) -> UXEvent:
    return UXEvent(
        kind="tool_blocked",
        session_id=session_id,
        data={
            "tool": tool,
            "command_preview": command_preview,
            "safety_level": safety_level,
            "session_id": session_id,
        },
    )


def tool_diff(
    *,
    tool: str,
    path: str,
    diff_text: str = "",
    diff_lines: list[str] | None = None,
    lines_changed: int | None = None,
    session_id: str = "",
) -> UXEvent:
    diff_lines = list(diff_lines or [])
    return UXEvent(
        kind="tool_diff",
        session_id=session_id,
        data={
            "tool": tool,
            "path": path,
            "diff_text": diff_text,
            "diff_lines": diff_lines,
            "lines_changed": lines_changed,
            "session_id": session_id,
        },
    )


def iteration_progress(current: int, max_iter: int) -> UXEvent:
    return UXEvent(kind="iteration_progress", data={"current": current, "max": max_iter})


def compaction_triggered(before: int, after: int) -> UXEvent:
    return UXEvent(kind="compaction_triggered", data={"before": before, "after": after})


def job_progress(
    *,
    job_kind: str,
    job_id: str,
    status: str,
    summary: str = "",
) -> UXEvent:
    return UXEvent(
        kind="job_progress",
        data={
            "job_kind": job_kind,
            "job_id": job_id,
            "status": status,
            "summary": summary,
        },
    )


def job_completed(
    *,
    job_kind: str,
    job_id: str,
    status: str,
    summary: str = "",
) -> UXEvent:
    return UXEvent(
        kind="job_completed",
        data={
            "job_kind": job_kind,
            "job_id": job_id,
            "status": status,
            "summary": summary,
        },
    )
