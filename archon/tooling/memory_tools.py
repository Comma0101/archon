"""Memory tool registrations."""

from archon import memory as memory_store


def register_memory_tools(registry) -> None:
    # 6. memory_read
    def memory_read(path: str = "") -> str:
        if path:
            text = memory_store.read(path)
            if not text:
                return f"Memory file not found: {path}"
            return text or "(empty)"
        files = memory_store.list_files()
        if not memory_store.MEMORY_DIR.exists():
            return "No memory directory yet."
        if not files:
            return "No memory files yet."
        return "Memory files:\n" + "\n".join(files)

    registry.register("memory_read", "Read a memory file or list all memory files", {
        "properties": {
            "path": {"type": "string", "description": "Relative path within memory dir (empty to list all)", "default": ""},
        },
        "required": [],
    }, memory_read)

    # 7. memory_write
    def memory_write(path: str, content: str) -> str:
        requested = str(path)
        actual = memory_store.write(path, content)
        if actual != requested:
            return (
                f"Wrote memory: {actual} ({len(content)} bytes) "
                f"[canonicalized from {requested}]"
            )
        return f"Wrote memory: {actual} ({len(content)} bytes)"

    registry.register("memory_write", "Write content to a persistent memory file", {
        "properties": {
            "path": {"type": "string", "description": "Relative path within memory dir (e.g. 'preferences.md')"},
            "content": {"type": "string", "description": "Content to write"},
        },
        "required": ["path", "content"],
    }, memory_write)

    # 8. memory_lookup
    def memory_lookup(query: str, limit: int = 5) -> str:
        q = (query or "").strip()
        if not q:
            return "Error: query is required"
        hits = memory_store.lookup(q, limit=max(1, min(int(limit), 20)))
        if not hits:
            return "No memory matches found."
        lines = ["Memory matches:"]
        for idx, hit in enumerate(hits, start=1):
            lines.extend([
                f"{idx}. path: {hit.get('path', '')}",
                f"   kind: {hit.get('kind', '')}",
                f"   layer: {hit.get('layer', '')}",
                f"   scope: {hit.get('scope', '')}",
                f"   score: {hit.get('score', 0)}",
            ])
            title = str(hit.get("title", "")).strip()
            if title:
                lines.append(f"   title: {title}")
            summary_text = str(hit.get("summary", "")).strip()
            if summary_text:
                lines.append(f"   summary: {summary_text}")
        return "\n".join(lines)

    registry.register(
        "memory_lookup",
        "Find relevant memory files using the machine-readable memory index. Use before broad memory_read when the user asks about previously saved preferences, project context, system profile, or prior decisions.",
        {
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What memory context you need (e.g. 'korami-site frontend', 'system hardware storage')",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results to return (1-20)",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
        memory_lookup,
    )

    # 9. memory_inbox_add
    def memory_inbox_add(
        kind: str,
        scope: str,
        summary: str,
        source: str = "",
        confidence: str = "medium",
        target_path: str = "",
        content: str = "",
    ) -> str:
        item = memory_store.inbox_add(
            kind=kind,
            scope=scope,
            summary=summary,
            source=source,
            confidence=confidence,
            target_path=target_path,
            content=content,
        )
        lines = [
            f"memory_inbox_id: {item.get('id', '')}",
            f"status: {item.get('status', '')}",
            f"kind: {item.get('kind', '')}",
            f"scope: {item.get('scope', '')}",
            f"confidence: {item.get('confidence', '')}",
        ]
        if item.get("target_path"):
            lines.append(f"target_path: {item['target_path']}")
        lines.append(f"summary: {item.get('summary', '')}")
        return "\n".join(lines)

    registry.register(
        "memory_inbox_add",
        "Queue a memory candidate for review instead of writing directly to canonical memory files. Use for inferred facts, decisions, or preferences that should be reviewed before persistence.",
        {
            "properties": {
                "kind": {"type": "string", "description": "Candidate type (e.g. preference, project_fact, decision, task_state)"},
                "scope": {"type": "string", "description": "Scope (e.g. global, project:korami-site)"},
                "summary": {"type": "string", "description": "Short summary of the memory candidate"},
                "source": {"type": "string", "description": "Where this came from (user_message, worker_session:..., file:...)", "default": ""},
                "confidence": {"type": "string", "description": "Confidence label (high|medium|low)", "default": "medium"},
                "target_path": {"type": "string", "description": "Optional target memory file to apply into later", "default": ""},
                "content": {"type": "string", "description": "Optional markdown content/snippet to write if applied", "default": ""},
            },
            "required": ["kind", "scope", "summary"],
        },
        memory_inbox_add,
    )

    # 10. memory_inbox_list
    def memory_inbox_list(status: str = "pending", limit: int = 20) -> str:
        items = memory_store.inbox_list(status=status, limit=max(1, min(int(limit), 100)))
        if not items:
            return "Memory inbox is empty."
        lines = ["Memory inbox:"]
        for item in items:
            lines.append(
                f"{item.get('id','')}  {item.get('status',''):<8} "
                f"{item.get('kind',''):<12} {item.get('scope','')}"
            )
            if item.get("target_path"):
                lines.append(f"  target_path: {item.get('target_path','')}")
            lines.append(f"  summary: {item.get('summary','')}")
        return "\n".join(lines)

    registry.register(
        "memory_inbox_list",
        "List queued memory candidates for review (pending by default).",
        {
            "properties": {
                "status": {"type": "string", "description": "Filter by status: pending|applied|rejected|all", "default": "pending"},
                "limit": {"type": "integer", "description": "Maximum entries to return (1-100)", "default": 20},
            },
            "required": [],
        },
        memory_inbox_list,
    )

    # 11. memory_inbox_decide
    def memory_inbox_decide(
        inbox_id: str,
        decision: str,
        target_path: str = "",
        apply_mode: str = "append",
        section_heading: str = "",
    ) -> str:
        item = memory_store.inbox_decide(
            inbox_id=inbox_id,
            decision=decision,
            target_path=target_path,
            apply_mode=apply_mode,
            section_heading=section_heading,
        )
        if item is None:
            return "Error: memory inbox item not found or invalid decision/target_path"
        lines = [
            f"memory_inbox_id: {item.get('id','')}",
            f"status: {item.get('status','')}",
            f"decision: {item.get('decision','')}",
        ]
        if item.get("apply_mode"):
            lines.append(f"apply_mode: {item.get('apply_mode','')}")
        if item.get("section_heading"):
            lines.append(f"section_heading: {item.get('section_heading','')}")
        if item.get("target_path"):
            lines.append(f"target_path: {item.get('target_path','')}")
        if item.get("applied_path"):
            lines.append(f"applied_path: {item.get('applied_path','')}")
        lines.append(f"summary: {item.get('summary','')}")
        return "\n".join(lines)

    registry.register(
        "memory_inbox_decide",
        "Approve/apply or reject a queued memory candidate. `apply` writes the candidate content (or a bullet from the summary) into the target memory file. Supports `apply_mode=append` (default) or `replace_section` with `section_heading` for safer updates to canonical memory files.",
        {
            "properties": {
                "inbox_id": {"type": "string", "description": "Memory inbox item ID"},
                "decision": {"type": "string", "description": "Decision: apply or reject"},
                "target_path": {"type": "string", "description": "Optional target memory file override when applying", "default": ""},
                "apply_mode": {"type": "string", "description": "Apply mode for `apply`: append (default) or replace_section", "default": "append"},
                "section_heading": {"type": "string", "description": "Section heading to replace when apply_mode=replace_section (e.g. '## GPU'). If omitted, the first heading in content is used.", "default": ""},
            },
            "required": ["inbox_id", "decision"],
        },
        memory_inbox_decide,
    )
