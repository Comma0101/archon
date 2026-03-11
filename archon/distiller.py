"""LLM-powered session distillation — extract structured learnings from conversations."""

from __future__ import annotations


DISTILLATION_PROMPT = """\
Analyze this conversation and extract structured learnings. For each item, output one line in this format:
KIND|CONFIDENCE|SCOPE|SUMMARY|TARGET_PATH

Where:
- KIND: fact, procedure, correction, preference, gap
- CONFIDENCE: high, medium, low
- SCOPE: global, or project:<name>
- SUMMARY: one-line description
- TARGET_PATH: suggested memory file (e.g., projects/browser-use.md, user/preferences.md, capability_gaps.md)

Rules:
- Only extract facts that are CONFIRMED in the conversation, not speculated
- Procedures should describe step-by-step processes that WORKED
- Corrections are where the user said something was wrong
- Gaps are things the user wanted but the assistant could not do
- Preferences are explicit user preferences stated in the conversation
- If nothing useful to extract, output: NONE

Conversation:
"""


def build_distillation_prompt(messages: list[dict], max_chars: int = 12000) -> str:
    """Build the prompt for session distillation."""
    lines = [DISTILLATION_PROMPT]
    total = 0
    for msg in messages:
        role = str(msg.get("role", "unknown"))
        content = _flatten_for_distillation(msg.get("content"))
        if not content:
            continue
        entry = f"{role}: {content}"
        if total + len(entry) > max_chars:
            break
        lines.append(entry)
        total += len(entry)
    return "\n".join(lines)


def parse_distillation_output(llm_output: str) -> list[dict]:
    """Parse structured distillation output into inbox-ready dicts."""
    items: list[dict] = []
    for line in llm_output.strip().splitlines():
        line = line.strip()
        if not line or line == "NONE":
            continue
        parts = line.split("|", 4)
        if len(parts) < 4:
            continue
        kind = parts[0].strip().lower()
        confidence = parts[1].strip().lower()
        scope = parts[2].strip()
        summary = parts[3].strip()
        target_path = parts[4].strip() if len(parts) > 4 else ""

        if kind not in ("fact", "procedure", "correction", "preference", "gap"):
            continue
        if confidence not in ("high", "medium", "low"):
            confidence = "medium"

        items.append({
            "kind": kind,
            "confidence": confidence,
            "scope": scope,
            "summary": summary,
            "target_path": target_path,
            "source": "session_distillation",
        })
    return items


def _flatten_for_distillation(content: object, max_chars: int = 400) -> str:
    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                elif item.get("type") == "tool_use":
                    parts.append(f"[used tool: {item.get('name', '')}]")
        text = " ".join(parts).strip()
    else:
        text = str(content or "").strip()
    return text[:max_chars] if len(text) > max_chars else text
