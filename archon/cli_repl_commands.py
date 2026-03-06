"""REPL slash-command handlers for Archon CLI."""

from __future__ import annotations

import os
import re
from typing import Callable

from archon.calls.store import list_call_job_summaries, load_call_job_summary
from archon.control.jobs import format_job_summary, format_job_summary_list
from archon.workers.session_store import list_worker_job_summaries, load_worker_job_summary


def handle_model_command(agent, text: str) -> tuple[bool, str]:
    """Handle `/model` command (show current provider/model)."""
    raw = (text or "").strip()
    if raw.lower() != "/model":
        return False, ""
    provider = str(getattr(agent.llm, "provider", "") or "").strip() or "unknown"
    model = str(getattr(agent.llm, "model", "") or "").strip() or "unknown"
    return True, f"Current model: {provider}/{model}"


def handle_model_list_command(text: str, model_catalog: dict[str, tuple[str, ...]]) -> tuple[bool, str]:
    """Handle `/model-list` command."""
    raw = (text or "").strip().lower()
    if raw != "/model-list":
        return False, ""
    lines = ["Available model presets:"]
    for provider, models in model_catalog.items():
        lines.append(f"- {provider}:")
        for model in models:
            lines.append(f"  - {model}")
    lines.append("Usage: /model-set <provider>-<model>")
    return True, "\n".join(lines)


def resolve_provider_credentials(
    llm_cfg,
    provider: str,
    *,
    env_getter: Callable[[str, str], str] | None = None,
) -> tuple[str, str]:
    """Resolve runtime credentials/base_url when switching providers from /model."""
    if env_getter is None:
        env_getter = os.environ.get

    provider_norm = str(provider or "").strip().lower()
    current_provider = str(getattr(llm_cfg, "provider", "") or "").strip().lower()
    if provider_norm == current_provider:
        return (
            str(getattr(llm_cfg, "api_key", "") or "").strip(),
            str(getattr(llm_cfg, "base_url", "") or "").strip(),
        )

    api_key = ""
    base_url = ""
    fallback_provider = str(getattr(llm_cfg, "fallback_provider", "") or "").strip().lower()
    if provider_norm == fallback_provider:
        api_key = str(getattr(llm_cfg, "fallback_api_key", "") or "").strip()
        base_url = str(getattr(llm_cfg, "fallback_base_url", "") or "").strip()

    if not api_key:
        if provider_norm == "google":
            api_key = str(env_getter("GEMINI_API_KEY", "")).strip()
        elif provider_norm == "openai":
            api_key = str(env_getter("OPENAI_API_KEY", "")).strip()
        elif provider_norm == "anthropic":
            api_key = str(env_getter("ANTHROPIC_API_KEY", "")).strip()
    return api_key, base_url


def handle_model_set_command(
    agent,
    text: str,
    *,
    llm_factory,
    resolve_provider_credentials_fn,
) -> tuple[bool, str]:
    """Handle `/model-set <provider>-<model>` command."""
    raw = (text or "").strip()
    if not raw.lower().startswith("/model-set"):
        return False, ""
    parts = raw.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip():
        return True, "Usage: /model-set <provider>-<model>"

    token = parts[1].strip()
    if " " in token:
        return True, "Usage: /model-set <provider>-<model> (no spaces)"

    provider_token, sep, model_token = token.partition("-")
    provider = provider_token.strip().lower()
    model = model_token.strip()
    if not sep or not provider or not model:
        return True, "Usage: /model-set <provider>-<model>"

    cfg = getattr(agent, "config", None)
    cfg_llm = getattr(cfg, "llm", None)
    if cfg_llm is None:
        return True, "Model switching unavailable: missing config.llm"

    if provider not in {"google", "openai", "anthropic"}:
        return True, "Unsupported provider. Use one of: google, openai, anthropic"
    if not model:
        return True, "Usage: /model-set <provider>-<model>"

    prev_provider = str(getattr(cfg_llm, "provider", "") or "")
    prev_model = str(getattr(cfg_llm, "model", "") or "")
    prev_api_key = str(getattr(cfg_llm, "api_key", "") or "")
    prev_base_url = str(getattr(cfg_llm, "base_url", "") or "")

    api_key, base_url = resolve_provider_credentials_fn(cfg_llm, provider)
    cfg_llm.provider = provider
    cfg_llm.model = model
    cfg_llm.api_key = api_key
    cfg_llm.base_url = base_url

    try:
        agent.llm = llm_factory(
            provider=provider,
            model=model,
            api_key=cfg_llm.api_key,
            temperature=float(getattr(cfg.agent, "temperature", 0.3)),
            base_url=cfg_llm.base_url,
        )
    except Exception as e:
        cfg_llm.provider = prev_provider
        cfg_llm.model = prev_model
        cfg_llm.api_key = prev_api_key
        cfg_llm.base_url = prev_base_url
        return True, f"Failed to set model: {e}"

    return True, f"Model set to: {provider}/{model}"


def set_calls_enabled_in_toml(text: str, enabled: bool) -> str:
    """Update or insert `[calls].enabled` while preserving unrelated config text."""
    value = "true" if enabled else "false"
    target_line = f"enabled = {value}"
    source = text or ""
    lines = source.splitlines()

    def _is_table_header(line: str) -> bool:
        stripped = line.strip()
        return stripped.startswith("[") and stripped.endswith("]")

    def _find_calls_section() -> tuple[int, int] | None:
        start = None
        for i, line in enumerate(lines):
            if line.strip() == "[calls]":
                start = i
                break
        if start is None:
            return None
        end = len(lines)
        for i in range(start + 1, len(lines)):
            if _is_table_header(lines[i]):
                end = i
                break
        return start, end

    calls_section = _find_calls_section()
    if calls_section is not None:
        start, end = calls_section
        for i in range(start + 1, end):
            if re.match(r"^\s*enabled\s*=", lines[i]):
                indent = re.match(r"^(\s*)", lines[i]).group(1) if re.match(r"^(\s*)", lines[i]) else ""
                lines[i] = f"{indent}{target_line}"
                out = "\n".join(lines)
                return out + ("\n" if source.endswith("\n") or out else "")
        lines.insert(start + 1, target_line)
        out = "\n".join(lines)
        return out + ("\n" if source.endswith("\n") or out else "")

    insert_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith("[calls."):
            insert_idx = i
            break

    block = ["[calls]", target_line]
    if insert_idx is None:
        out_lines = list(lines)
        if out_lines and out_lines[-1].strip():
            out_lines.append("")
        out_lines.extend(block)
        out = "\n".join(out_lines)
        return out + ("\n" if out else "")

    out_lines = list(lines[:insert_idx])
    if out_lines and out_lines[-1].strip():
        out_lines.append("")
    out_lines.extend(block)
    out_lines.append("")
    out_lines.extend(lines[insert_idx:])
    out = "\n".join(out_lines)
    return out + ("\n" if source.endswith("\n") or out else "")


def handle_calls_command(
    agent,
    text: str,
    *,
    load_config_fn,
    set_calls_enabled_config_fn,
) -> tuple[bool, str]:
    """Handle `/calls` command (status/on/off), with `/call` alias support."""
    raw = (text or "").strip()
    parts = raw.split(maxsplit=1)
    cmd = parts[0].lower() if parts else ""
    if cmd not in {"/calls", "/call"}:
        return False, ""

    arg = parts[1].strip().lower() if len(parts) > 1 else "status"
    if arg in {"status", ""}:
        cfg = load_config_fn()
        enabled = bool(getattr(getattr(cfg, "calls", None), "enabled", False))
        base_url = str(getattr(getattr(cfg.calls, "voice_service", None), "base_url", "") or "")
        status = "enabled" if enabled else "disabled"
        extra = f" | base_url={base_url}" if base_url else ""
        return True, f"Calls: {status}{extra}"

    if arg in {"on", "enable", "enabled"}:
        path = set_calls_enabled_config_fn(True)
        if hasattr(agent, "config") and hasattr(getattr(agent, "config"), "calls"):
            agent.config.calls.enabled = True
        return True, f"Calls enabled in {path}"

    if arg in {"off", "disable", "disabled"}:
        path = set_calls_enabled_config_fn(False)
        if hasattr(agent, "config") and hasattr(getattr(agent, "config"), "calls"):
            agent.config.calls.enabled = False
        return True, f"Calls disabled in {path}"

    return True, "Usage: /calls [status|on|off] (alias: /call)"


def handle_profile_command(agent, text: str) -> tuple[bool, str]:
    """Handle `/profile` command (show/set policy profile)."""
    raw = (text or "").strip()
    parts = raw.split()
    if not parts or parts[0].lower() != "/profile":
        return False, ""

    sub = parts[1].strip().lower() if len(parts) > 1 else "show"
    cfg_profiles = getattr(getattr(agent, "config", None), "profiles", {}) or {}
    available = [str(name) for name in cfg_profiles.keys()] if isinstance(cfg_profiles, dict) else []
    if not available:
        available = ["default"]

    if sub in {"show", "status", "list"}:
        active = str(getattr(agent, "policy_profile", "") or "").strip() or "default"
        return True, f"Policy profile: {active} | available: {', '.join(available)}"

    if sub == "set":
        if len(parts) < 3 or not parts[2].strip():
            return True, "Usage: /profile [show|set <name>]"
        profile_name = parts[2].strip()
        if profile_name not in available:
            return True, f"Unknown profile '{profile_name}'. Available: {', '.join(available)}"
        setter = getattr(agent, "set_policy_profile", None)
        if callable(setter):
            setter(profile_name)
        else:
            setattr(agent, "policy_profile", profile_name)
        return True, f"Policy profile set to: {profile_name}"

    return True, "Usage: /profile [show|set <name>]"


def _collect_job_summaries(limit: int = 10):
    max_items = max(1, int(limit))
    jobs = list_worker_job_summaries(limit=max_items) + list_call_job_summaries(limit=max_items)
    jobs.sort(key=lambda job: (job.last_update_at, job.job_id), reverse=True)
    return jobs[:max_items]


def _load_job_summary(job_ref: str):
    ref = str(job_ref or "").strip()
    if not ref:
        return None
    if ref.startswith("worker:"):
        return load_worker_job_summary(ref.split(":", 1)[1])
    if ref.startswith("call:"):
        return load_call_job_summary(ref.split(":", 1)[1])
    job = load_worker_job_summary(ref)
    if job is not None:
        return job
    return load_call_job_summary(ref)


def handle_jobs_command(agent, text: str) -> tuple[bool, str]:
    """Handle `/jobs` command (list recent cross-surface jobs)."""
    _ = agent
    raw = (text or "").strip()
    parts = raw.split()
    if not parts or parts[0].lower() != "/jobs":
        return False, ""

    limit = 10
    if len(parts) > 1:
        try:
            limit = int(parts[1])
        except ValueError:
            return True, "Usage: /jobs [limit]"

    jobs = _collect_job_summaries(limit=limit)
    if not jobs:
        return True, "Jobs: none"
    return True, "Jobs:\n" + format_job_summary_list(jobs)


def handle_job_command(agent, text: str) -> tuple[bool, str]:
    """Handle `/job <id>` command (show one normalized job summary)."""
    _ = agent
    raw = (text or "").strip()
    parts = raw.split(maxsplit=1)
    if not parts or parts[0].lower() != "/job":
        return False, ""
    if len(parts) < 2 or not parts[1].strip():
        return True, "Usage: /job <id>"

    job = _load_job_summary(parts[1].strip())
    if job is None:
        return True, f"Job not found: {parts[1].strip()}"
    return True, format_job_summary(job)


def handle_repl_command(
    agent,
    text: str,
    *,
    slash_commands,
    handle_calls_command_fn,
    handle_profile_command_fn,
    handle_model_list_command_fn,
    handle_model_set_command_fn,
    handle_model_command_fn,
) -> tuple[str | None, str]:
    """Handle slash commands. Returns (action, message)."""
    raw = (text or "").strip()
    if not raw.startswith("/"):
        return None, ""
    if raw == "/":
        lines = ["Available commands:"]
        for name, desc in slash_commands:
            lines.append(f"  {name:<10} {desc}")
        return "help", "\n".join(lines)
    if raw.lower() == "/reset":
        return "reset", ""
    if raw.lower() in {"/help", "/?"}:
        return (
            "help",
            "Commands: /help, /reset, /model, /model-list, /model-set <provider>-<model>, /calls [status|on|off], /profile [show|set <name>], /jobs [limit], /job <id>, /paste\n"
            "Multiline paste: paste normally (bracketed paste) or use /paste fallback, end with /end.",
        )
    handled, msg = handle_jobs_command(agent, raw)
    if handled:
        return "jobs", msg
    handled, msg = handle_job_command(agent, raw)
    if handled:
        return "job", msg
    handled, msg = handle_calls_command_fn(agent, raw)
    if handled:
        return "calls", msg
    handled, msg = handle_profile_command_fn(agent, raw)
    if handled:
        return "profile", msg
    handled, msg = handle_model_list_command_fn(raw)
    if handled:
        return "model", msg
    handled, msg = handle_model_set_command_fn(agent, raw)
    if handled:
        return "model", msg
    handled, msg = handle_model_command_fn(agent, raw)
    if handled:
        return "model", msg
    return None, ""
