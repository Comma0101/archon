from __future__ import annotations

import argparse
import os
import sys
import time

from archon.config import load_config
from archon.control.hooks import HookBus
from archon.research.google_deep_research import (
    DEFAULT_DEEP_RESEARCH_AGENT,
    GoogleDeepResearchClient,
)
from archon.research.store import load_research_job, start_research_stream_job


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _load_cfg():
    try:
        return load_config()
    except Exception:
        return None


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a bounded live Deep Research smoke test.")
    parser.add_argument("--prompt", required=True, help="Deep Research prompt to run")
    parser.add_argument("--timeout", type=float, default=90.0, help="Seconds to wait for stream evidence")
    return parser.parse_args(argv)


def _deep_research_settings(cfg) -> tuple[str, str, int]:
    deep_cfg = getattr(getattr(cfg, "research", None), "google_deep_research", None)
    agent = str(getattr(deep_cfg, "agent", DEFAULT_DEEP_RESEARCH_AGENT) or DEFAULT_DEEP_RESEARCH_AGENT).strip()
    thinking_summaries = str(getattr(deep_cfg, "thinking_summaries", "auto") or "auto").strip().lower() or "auto"
    timeout_minutes = max(1, int(getattr(deep_cfg, "timeout_minutes", 20) or 20))
    return agent or DEFAULT_DEEP_RESEARCH_AGENT, thinking_summaries, timeout_minutes


def _google_api_key_from_cfg(cfg) -> str:
    llm_cfg = getattr(cfg, "llm", None)
    provider = str(getattr(llm_cfg, "provider", "") or "").strip().lower()
    base_url = str(getattr(llm_cfg, "base_url", "") or "").strip().lower()
    if provider == "google" or (provider == "openai" and "googleapis" in base_url):
        return str(getattr(llm_cfg, "api_key", "") or "").strip()
    fallback_provider = str(getattr(llm_cfg, "fallback_provider", "") or "").strip().lower()
    fallback_base_url = str(getattr(llm_cfg, "fallback_base_url", "") or "").strip().lower()
    if fallback_provider == "google" or (fallback_provider == "openai" and "googleapis" in fallback_base_url):
        return str(getattr(llm_cfg, "fallback_api_key", "") or "").strip()
    return ""


def _find_api_key(cfg) -> str:
    env_key = str(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
    if env_key:
        return env_key
    return _google_api_key_from_cfg(cfg)


def _record_has_stream_evidence(record) -> bool:
    if record is None:
        return False
    if str(getattr(record, "status", "") or "").strip().lower() in {"completed", "done", "failed", "error", "cancelled"}:
        return True
    if max(0, int(getattr(record, "event_count", 0) or 0)) > 0:
        return True
    if str(getattr(record, "last_event_id", "") or "").strip():
        return True
    if str(getattr(record, "latest_thought_summary", "") or "").strip():
        return True
    return False


def _record_is_terminal(record) -> bool:
    return str(getattr(record, "status", "") or "").strip().lower() in {
        "completed",
        "done",
        "failed",
        "error",
        "cancelled",
    }


def _print_snapshot(record) -> None:
    lines = [
        f"job_id: research:{getattr(record, 'interaction_id', '')}",
        f"status: {getattr(record, 'status', '')}",
        f"provider_status: {getattr(record, 'provider_status', '')}",
        f"stream_status: {getattr(record, 'stream_status', '')}",
        f"last_event_id: {getattr(record, 'last_event_id', '')}",
        f"latest_thought_summary: {getattr(record, 'latest_thought_summary', '')}",
        f"event_count: {max(0, int(getattr(record, 'event_count', 0) or 0))}",
        f"poll_count: {max(0, int(getattr(record, 'poll_count', 0) or 0))}",
        f"error: {getattr(record, 'error', '')}",
    ]
    print("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    cfg = _load_cfg()
    api_key = _find_api_key(cfg)
    if not api_key:
        print("Missing Google API key. Set GEMINI_API_KEY or GOOGLE_API_KEY, or configure Google in Archon.", file=sys.stderr)
        return 1

    agent, thinking_summaries, timeout_minutes = _deep_research_settings(cfg)
    client = GoogleDeepResearchClient.from_api_key(
        api_key,
        agent=agent,
        thinking_summaries=thinking_summaries,
    )
    hook_bus = HookBus()

    def _on_progress(hook_event) -> None:
        event = (getattr(hook_event, "payload", None) or {}).get("event")
        if event is not None and hasattr(event, "render_text"):
            print(event.render_text())

    hook_bus.register("ux.job_progress", _on_progress)

    def _on_completed(hook_event) -> None:
        event = (getattr(hook_event, "payload", None) or {}).get("event")
        if event is not None and hasattr(event, "render_text"):
            print(f"[COMPLETED] {event.render_text()}")

    hook_bus.register("ux.job_completed", _on_completed)

    record = start_research_stream_job(
        args.prompt,
        client=client,
        agent_name=agent,
        timeout_minutes=timeout_minutes,
        hook_bus=hook_bus,
    )
    interaction_id = str(getattr(record, "interaction_id", "") or "").strip()
    print(f"started: research:{interaction_id}")

    deadline = time.monotonic() + max(0.1, float(args.timeout or 0))
    latest = None
    saw_evidence = False
    while time.monotonic() < deadline:
        latest = load_research_job(interaction_id)
        if latest is not None:
            saw_evidence = saw_evidence or _record_has_stream_evidence(latest)
            if _record_is_terminal(latest):
                break
        _sleep(0.25)

    latest = load_research_job(interaction_id) or latest
    if latest is not None:
        _print_snapshot(latest)

    if saw_evidence:
        return 0
    print("No Deep Research stream evidence observed before timeout.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
