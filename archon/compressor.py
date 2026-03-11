"""LLM-powered context compression that writes to the same compaction path."""

from __future__ import annotations


COMPRESSION_SYSTEM_PROMPT = (
    "You are a context compression assistant. Summarize the conversation "
    "preserving:\n"
    "- What task was being worked on and its current state\n"
    "- Key decisions made and why\n"
    "- Errors encountered and their root causes\n"
    "- Important facts discovered (project details, paths, commands)\n"
    "- What remains to be done\n\n"
    "Be concise but preserve actionable information. Write in past tense."
)


def build_compression_prompt(messages: list[dict], max_chars: int = 8000) -> str:
    """Build a prompt for LLM-based compression of conversation history."""
    lines = ["Summarize this conversation history:\n"]
    total_chars = 0
    for msg in messages:
        role = str(msg.get("role", "unknown"))
        content = _flatten(msg.get("content"))
        entry = f"{role}: {content}"
        if total_chars + len(entry) > max_chars:
            entry = entry[:max_chars - total_chars]
            lines.append(entry)
            break
        lines.append(entry)
        total_chars += len(entry)
    return "\n".join(lines)


def parse_compression_result(
    llm_output: str,
    *,
    layer: str = "session",
    summary_id: str = "latest",
) -> dict:
    """Package LLM output into a compaction artifact dict."""
    content = llm_output.strip()
    if not content:
        content = "(No summary generated)"
    title = "# Session Compaction Summary" if layer == "session" else "# Task Compaction Summary"
    return {
        "layer": layer,
        "summary_id": summary_id,
        "content": f"{title}\n\n{content}\n",
        "summary": content[:240],
    }


def _flatten(content: object, max_chars: int = 500) -> str:
    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                t = item.get("type", "")
                if t == "text":
                    parts.append(str(item.get("text", "")))
                elif t == "tool_use":
                    parts.append(f"[tool: {item.get('name', '')}]")
                elif t == "tool_result":
                    parts.append(f"[result: {str(item.get('content', ''))[:100]}]")
            elif isinstance(item, str):
                parts.append(item)
        text = " ".join(parts).strip()
    else:
        text = str(content or "").strip()
    return text[:max_chars] if len(text) > max_chars else text
