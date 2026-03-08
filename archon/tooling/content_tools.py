"""News, web retrieval, and deep research tool registrations."""

import os

from archon.config import ensure_dirs, load_config
from archon.news.runner import get_or_build_news_digest, send_digest_to_telegram
from archon.web.read import read_web_url
from archon.web.search import search_web

from .common import truncate_text


def register_content_tools(registry) -> None:
    def _resolve_deep_research_api_key(cfg) -> str:
        llm_cfg = getattr(cfg, "llm", None)
        api_key = ""
        if str(getattr(llm_cfg, "provider", "") or "").strip().lower() == "google":
            api_key = str(getattr(llm_cfg, "api_key", "") or "").strip()
        if not api_key and str(getattr(llm_cfg, "fallback_provider", "") or "").strip().lower() == "google":
            api_key = str(getattr(llm_cfg, "fallback_api_key", "") or "").strip()
        if not api_key:
            api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not api_key:
            api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
        return api_key

    def _build_deep_research_client(cfg):
        deep_cfg = getattr(getattr(cfg, "research", None), "google_deep_research", None)
        if deep_cfg is None or not bool(getattr(deep_cfg, "enabled", False)):
            return None
        api_key = _resolve_deep_research_api_key(cfg)
        if not api_key:
            return None
        from archon.research.google_deep_research import GoogleDeepResearchClient

        agent_name = str(getattr(deep_cfg, "agent", "") or "").strip()
        return GoogleDeepResearchClient.from_api_key(api_key, agent=agent_name)

    # 12. news_brief
    def news_brief(force_refresh: bool = False, send_to_telegram: bool = False) -> str:
        ensure_dirs()
        cfg = load_config()
        result = get_or_build_news_digest(cfg, force_refresh=bool(force_refresh))
        if result.digest is None:
            lines = [
                f"news status: {result.status}",
                f"reason: {result.reason or '(none)'}",
            ]
            return "\n".join(lines)

        lines = [
            f"news status: {result.status}",
            f"reason: {result.reason or '(none)'}",
            f"date: {result.digest.date_iso}",
            f"items: {result.digest.item_count}",
            f"fallback: {result.digest.used_fallback}",
        ]

        if send_to_telegram:
            if not cfg.news.telegram.send_enabled:
                lines.append("telegram_delivery: skipped (news.telegram.send_enabled=false)")
            else:
                try:
                    send_digest_to_telegram(cfg, result.digest.markdown)
                    lines.append("telegram_delivery: sent")
                except Exception as e:
                    lines.append(f"telegram_delivery: error ({type(e).__name__}: {e})")

        lines.extend(["", result.digest.markdown])
        return "\n".join(lines)

    registry.register(
        "news_brief",
        "Build or retrieve today's AI news digest (uses cached digest by default). "
        "Use this when the user asks for today's AI news, briefing, or digest.",
        {
            "properties": {
                "force_refresh": {
                    "type": "boolean",
                    "description": "Rebuild today's digest instead of using cached result",
                    "default": False,
                },
                "send_to_telegram": {
                    "type": "boolean",
                    "description": "Also send digest to configured news.telegram.chat_ids",
                    "default": False,
                },
            },
            "required": [],
        },
        news_brief,
    )

    # 13. web_search
    def web_search_tool(
        query: str,
        limit: int = 5,
        domains: list[str] | None = None,
        recency_days: int | None = None,
    ) -> str:
        ensure_dirs()
        cfg = load_config()
        if not cfg.web.enabled:
            return "Web tools are disabled (set [web].enabled = true)."

        results, meta = search_web(
            query=query,
            config=cfg,
            limit=limit,
            domains=domains,
            recency_days=recency_days,
        )
        provider = meta.get("provider", "unknown")
        notes = meta.get("notes") or []
        lines = [
            f"web_search provider: {provider}",
            f"query: {query}",
            f"results: {len(results)}",
        ]
        if notes:
            lines.append("notes: " + ", ".join(str(n) for n in notes))
        if not results:
            lines.append("No results found.")
            return "\n".join(lines)

        for i, result in enumerate(results, start=1):
            lines.append("")
            lines.append(f"{i}. {result.title}")
            lines.append(f"URL: {result.url}")
            if result.source:
                lines.append(f"Source: {result.source}")
            if result.snippet:
                lines.append(f"Snippet: {truncate_text(result.snippet, 500)}")
        return "\n".join(lines)

    registry.register(
        "web_search",
        "Search the public web for current information. Use this for latest/current/today questions before answering.",
        {
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (1-10, default 5)",
                    "default": 5,
                },
                "domains": {
                    "type": "array",
                    "description": "Optional domain allowlist (e.g. ['anthropic.com','openai.com'])",
                    "items": {"type": "string"},
                },
                "recency_days": {
                    "type": "integer",
                    "description": "Optional recency preference in days (provider-dependent)",
                },
            },
            "required": ["query"],
        },
        web_search_tool,
    )

    # 14. web_read
    def web_read_tool(url: str, max_chars: int = 6000) -> str:
        ensure_dirs()
        cfg = load_config()
        if not cfg.web.enabled:
            return "Web tools are disabled (set [web].enabled = true)."

        page = read_web_url(url, config=cfg, max_chars=max_chars)
        lines = [
            f"URL: {page.url}",
            f"Final URL: {page.final_url}",
            f"Content-Type: {page.content_type}",
        ]
        if page.title:
            lines.append(f"Title: {page.title}")
        lines.extend(["", page.text])
        return "\n".join(lines)

    registry.register(
        "web_read",
        "Fetch and read a public web page URL (HTTP/HTTPS only). Use after web_search to inspect sources before answering.",
        {
            "properties": {
                "url": {"type": "string", "description": "HTTP or HTTPS URL to fetch"},
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum characters of extracted text to return",
                    "default": 6000,
                },
            },
            "required": ["url"],
        },
        web_read_tool,
    )

    # 15. deep_research
    def deep_research_tool(query: str) -> str:
        """Start a Google Deep Research job for comprehensive analysis."""
        ensure_dirs()
        cfg = load_config()
        deep_cfg = getattr(getattr(cfg, "research", None), "google_deep_research", None)
        if deep_cfg is None or not bool(getattr(deep_cfg, "enabled", False)):
            return (
                "Deep Research unavailable: disabled in config. "
                "Enable [research.google_deep_research].enabled to use."
            )

        from archon.research.models import ResearchJobRecord
        from archon.research.store import save_research_job
        from datetime import datetime, timezone

        client = _build_deep_research_client(cfg)
        if client is None:
            return "Deep Research unavailable: no GEMINI_API_KEY configured."

        agent_name = str(getattr(deep_cfg, "agent", "") or "").strip()
        try:
            interaction = client.start_research(query)
        except Exception as e:
            return f"Deep Research failed to start: {type(e).__name__}: {e}"

        timestamp = datetime.now(timezone.utc).isoformat()
        record = save_research_job(
            ResearchJobRecord(
                interaction_id=str(getattr(interaction, "interaction_id", "") or "").strip(),
                status=str(getattr(interaction, "status", "") or "running").strip() or "running",
                prompt=query,
                agent=agent_name,
                created_at=timestamp,
                updated_at=timestamp,
                summary="Research job started",
                output_text=str(getattr(interaction, "output_text", "") or ""),
                error="",
                provider_status=str(getattr(interaction, "status", "") or "running").strip() or "running",
                timeout_minutes=max(1, int(getattr(deep_cfg, "timeout_minutes", 20) or 20)),
            )
        )
        try:
            from archon.research.store import start_research_job_monitor

            poll_interval = int(getattr(deep_cfg, "poll_interval_sec", 10) or 10)
            start_research_job_monitor(
                record.interaction_id,
                refresh_client=client,
                poll_interval_sec=poll_interval,
                hook_bus=getattr(registry, "hook_bus", None),
            )
        except Exception:
            pass
        job_id = f"research:{record.interaction_id}"
        return (
            f"Research job started: {job_id}\n"
            f"Use /jobs or /job {job_id} to inspect progress."
        )

    registry.register(
        "deep_research",
        "Start a Google Deep Research job for comprehensive, multi-source analysis of a topic. "
        "Use this when the user explicitly asks for deep research, thorough investigation, "
        "or comprehensive analysis that goes beyond a simple web search. "
        "Do NOT use this for casual questions — only when the user wants in-depth research.",
        {
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The research query/topic to investigate in depth",
                },
            },
            "required": ["query"],
        },
        deep_research_tool,
    )

    # 16. check_research_job
    def check_research_job_tool(job_id: str) -> str:
        """Check the status of a deep research job."""
        ensure_dirs()
        cfg = load_config()
        from archon.research.store import load_research_job

        # Strip "research:" prefix if present
        interaction_id = job_id
        if interaction_id.startswith("research:"):
            interaction_id = interaction_id[9:]

        refresh_client = _build_deep_research_client(cfg)
        record = load_research_job(interaction_id, refresh_client=refresh_client)
        if record is None:
            return f"Research job '{job_id}' not found."

        lines = [
            f"Job: research:{record.interaction_id}",
            f"Status: {record.status}",
            f"Prompt: {record.prompt}",
            f"Updated: {record.updated_at}",
        ]
        if record.summary:
            lines.append(f"Summary: {record.summary}")
        if record.output_text:
            lines.append(f"Output:\n{record.output_text[:3000]}")
        if record.error:
            lines.append(f"Error: {record.error}")
        return "\n".join(lines)

    registry.register(
        "check_research_job",
        "Check the status of a running deep research job. "
        "Use this to poll for progress instead of shelling out to Python.",
        {
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "The research job ID (e.g. 'research:v1_xxx' or just 'v1_xxx')",
                },
            },
            "required": ["job_id"],
        },
        check_research_job_tool,
    )

    # 17. list_research_jobs
    def list_research_jobs_tool(limit: int = 10) -> str:
        """List recent deep research jobs so the agent can inspect/poll them."""
        ensure_dirs()
        cfg = load_config()
        from archon.research.store import list_research_jobs

        refresh_client = _build_deep_research_client(cfg)
        records = list_research_jobs(limit=max(1, int(limit)), refresh_client=refresh_client)
        lines = [f"Research jobs: {len(records)}"]
        if not records:
            lines.append("No research jobs found.")
            return "\n".join(lines)

        for record in records:
            summary = truncate_text(str(getattr(record, "summary", "") or ""), 160)
            if not summary:
                summary = truncate_text(str(getattr(record, "prompt", "") or ""), 160)
            if not summary:
                summary = "No summary"
            lines.append(
                f"- research:{record.interaction_id} | {record.status} | {summary}"
            )
        return "\n".join(lines)

    registry.register(
        "list_research_jobs",
        "List recent deep research jobs so you can inspect existing job ids before polling one. "
        "Use this when the user asks about current/past research jobs or how many research jobs exist.",
        {
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of recent research jobs to list",
                    "default": 10,
                },
            },
            "required": [],
        },
        list_research_jobs_tool,
    )
