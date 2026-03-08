"""REPL slash-command handlers for Archon CLI."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Callable

from archon.calls.store import list_call_job_summaries, load_call_job_summary
from archon.control.jobs import format_job_summary, format_job_summary_list
from archon.control.policy import resolve_profile
from archon.control.skills import (
    ensure_session_skill_profile,
    get_builtin_skill,
    is_session_skill_profile_name,
    list_builtin_skills,
)
from archon.mcp import MCPClient
from archon.research.store import (
    cancel_research_job,
    list_research_job_summaries,
    load_research_job,
    load_research_job_summary,
    purge_completed_jobs,
    start_research_job_monitor,
)
from archon.workers.session_store import list_worker_job_summaries, load_worker_job_summary, purge_stale_sessions

_NATIVE_PLUGIN_SPECS = (
    ("calls", "config.calls", lambda cfg: bool(getattr(getattr(cfg, "calls", None), "enabled", False))),
    ("telegram", "config.telegram", lambda cfg: bool(getattr(getattr(cfg, "telegram", None), "enabled", False))),
    ("web", "config.web", lambda cfg: bool(getattr(getattr(cfg, "web", None), "enabled", False))),
)
_SKILL_REQUEST_ALIASES = {
    "general": "general",
    "coder": "coder",
    "research": "researcher",
    "researcher": "researcher",
    "operator": "operator",
    "sales": "sales",
    "memory curator": "memory_curator",
    "memory_curator": "memory_curator",
}
_SKILL_REQUEST_PATTERN = "|".join(
    sorted((re.escape(alias) for alias in _SKILL_REQUEST_ALIASES), key=len, reverse=True)
)
_SKILL_REQUEST_PREFIX = r"(?:please\s+)?(?:archon[,:]?\s+)?(?:can you\s+)?(?:could you\s+)?(?:let'?s\s+)?"
_EXPLICIT_SKILL_PATTERNS = (
    re.compile(rf"^\s*{_SKILL_REQUEST_PREFIX}use (?P<skill>{_SKILL_REQUEST_PATTERN}) skill\b", re.IGNORECASE),
    re.compile(rf"^\s*{_SKILL_REQUEST_PREFIX}switch to (?P<skill>{_SKILL_REQUEST_PATTERN})(?: skill| mode)?\b", re.IGNORECASE),
    re.compile(rf"^\s*{_SKILL_REQUEST_PREFIX}act as(?: an?| the)? (?P<skill>{_SKILL_REQUEST_PATTERN})(?: skill)?\b", re.IGNORECASE),
    re.compile(rf"^\s*{_SKILL_REQUEST_PREFIX}enter (?P<skill>{_SKILL_REQUEST_PATTERN}) mode\b", re.IGNORECASE),
)
_TERMINAL_HELP_TEXT = (
    "Core: /status, /approvals, /jobs, /skills, /mcp, /reset, /clear\n"
    "Advanced: /cost, /compact, /context, /doctor, /permissions, /plugins, /model, "
    "/calls, /profile, /job <id>, /paste\n"
    "Use / to browse commands."
)


def handle_model_command(agent, text: str) -> tuple[bool, str]:
    """Handle `/model` command (show current provider/model)."""
    raw = (text or "").strip()
    if raw.lower() != "/model":
        return False, ""
    llm = getattr(agent, "llm", None)
    cfg_llm = getattr(getattr(agent, "config", None), "llm", None)
    provider = str(getattr(llm, "provider", "") or getattr(cfg_llm, "provider", "") or "").strip() or "unknown"
    model = str(getattr(llm, "model", "") or getattr(cfg_llm, "model", "") or "").strip() or "unknown"
    return True, f"Current model: {provider}/{model}"


def handle_status_command(agent, text: str) -> tuple[bool, str]:
    """Handle `/status` command with a compact shell summary."""
    raw = (text or "").strip().lower()
    if raw != "/status":
        return False, ""

    cfg = getattr(agent, "config", None)
    llm = getattr(agent, "llm", None)
    provider = str(getattr(llm, "provider", "") or getattr(getattr(cfg, "llm", None), "provider", "") or "").strip() or "unknown"
    model = str(getattr(llm, "model", "") or getattr(getattr(cfg, "llm", None), "model", "") or "").strip() or "unknown"
    profile_display, _resolved_name, _profile, _profile_missing = _resolve_profile_diagnostics(agent, cfg)
    total_tokens = max(
        0,
        int(getattr(agent, "total_input_tokens", 0) or 0) + int(getattr(agent, "total_output_tokens", 0) or 0),
    )
    parts = [
        f"model={provider}/{model}",
        f"profile={profile_display}",
    ]
    active_skill = _active_skill_name(agent)
    if active_skill:
        parts.append(f"skill={active_skill}")
    orchestrator_mode = _describe_orchestrator_mode(cfg)
    if orchestrator_mode != "legacy":
        parts.append(f"orchestrator={orchestrator_mode}")
    calls_state = "on" if bool(getattr(getattr(cfg, "calls", None), "enabled", False)) else "off"
    parts.append(f"calls={calls_state}")
    parts.append(f"mcp={_format_mcp_counts(cfg)}")
    parts.append(f"tokens={total_tokens:,}")
    return True, "Status: " + " | ".join(parts)



def handle_cost_command(agent, text: str) -> tuple[bool, str]:
    """Handle /cost command to show estimated session cost."""
    raw = (text or "").strip().lower()
    if raw != "/cost":
        return False, ""

    total_input = max(0, int(getattr(agent, "total_input_tokens", 0) or 0))
    total_output = max(0, int(getattr(agent, "total_output_tokens", 0) or 0))
    total_tokens = total_input + total_output
    return True, f"Cost: total_tokens={total_tokens:,} | input={total_input:,} | output={total_output:,}"


def handle_doctor_command(agent, text: str) -> tuple[bool, str]:
    """Handle `/doctor` command with lightweight local health checks."""
    raw = (text or "").strip().lower()
    if raw != "/doctor":
        return False, ""

    cfg = getattr(agent, "config", None)
    llm_ready = _llm_runtime_ready(agent, cfg)
    profile_display, _resolved_name, _profile, profile_missing = _resolve_profile_diagnostics(agent, cfg)
    calls_state = "on" if bool(getattr(getattr(cfg, "calls", None), "enabled", False)) else "off"
    return True, (
        "Doctor: "
        f"llm={'ok' if llm_ready else 'missing'} | "
        f"profile={'ok' if not profile_missing else profile_display} | "
        f"calls={calls_state} | "
        f"mcp={_format_mcp_counts(cfg)}"
    )


def handle_permissions_command(agent, text: str) -> tuple[bool, str]:
    """Handle `/permissions` command with compact policy details."""
    raw = (text or "").strip()
    parts = raw.split()
    if not parts or parts[0].lower() != "/permissions":
        return False, ""
    mode = parts[1].strip().lower() if len(parts) > 1 else ""
    if len(parts) > 2 or mode not in {"", "auto", "accept_reads", "confirm_all"}:
        return True, "Usage: /permissions [auto|accept_reads|confirm_all]"

    cfg = getattr(agent, "config", None)
    safety_cfg = getattr(cfg, "safety", None)
    if mode:
        if safety_cfg is None:
            return True, "Permissions unavailable: missing config.safety"
        safety_cfg.permission_mode = mode
    profile_display, _resolved_name, profile, _profile_missing = _resolve_profile_diagnostics(agent, cfg)
    allowed_tools = sorted(str(item).strip() for item in profile.allowed_tools if str(item).strip())
    skill_suffix = f" | skill={profile.skill_name}" if getattr(profile, "skill_name", "") else ""
    permission_mode = str(getattr(safety_cfg, "permission_mode", "confirm_all") or "confirm_all").strip().lower()
    return True, (
        "Permissions: "
        f"permission_mode={permission_mode} | "
        f"profile={profile_display}{skill_suffix} | "
        f"mode={profile.max_mode} | "
        f"tools={len(allowed_tools)} [{','.join(allowed_tools)}]"
    )


def handle_compact_command(agent, text: str) -> tuple[bool, str]:
    """Handle /compact command to manually compact conversation history."""
    raw = (text or "").strip().lower()
    if raw != "/compact":
        return False, ""

    result = agent.compact_context()
    compacted = result.get("compacted_messages", 0)
    path = result.get("path", "")
    summary = result.get("summary", "")
    return True, f"Compact: history_messages={compacted} | path={path} | summary={summary}"


def handle_clear_command(agent, text: str) -> tuple[bool, str]:
    """Handle /clear — reset conversation history."""
    raw = (text or "").strip().lower()
    if raw != "/clear":
        return False, ""
    count = len(agent.history)
    agent.history.clear()
    agent.total_input_tokens = 0
    agent.total_output_tokens = 0
    return True, f"Cleared {count} messages. Fresh start."


def handle_context_command(agent, text: str) -> tuple[bool, str]:
    """Handle /context command to show current context usage."""
    raw = (text or "").strip().lower()
    if raw != "/context":
        return False, ""

    history = getattr(agent, "history", []) or []
    messages = len(history)
    pending = getattr(agent, "_pending_compactions", []) or []
    pending_count = len(pending)
    return True, f"Context: history_messages={messages} | pending_compactions={pending_count}"


def handle_skills_command(agent, text: str) -> tuple[bool, str]:
    """Handle `/skills` command (list/show/use/clear)."""
    raw = (text or "").strip()
    parts = raw.split()
    if not parts or parts[0].lower() != "/skills":
        return False, ""

    sub = parts[1].strip().lower() if len(parts) > 1 else "list"
    skills = list_builtin_skills()
    available_names = [skill.name for skill in skills]

    if sub in {"list", "show", "status"} and len(parts) == 1:
        active_skill = _active_skill_name(agent)
        active_label = active_skill or "none"
        return True, f"Skills: active={active_label} | available={', '.join(available_names)}"

    if sub == "list":
        active_skill = _active_skill_name(agent)
        active_label = active_skill or "none"
        return True, f"Skills: active={active_label} | available={', '.join(available_names)}"

    if sub == "show":
        if len(parts) < 3 or not parts[2].strip():
            return True, "Usage: /skills [list|show <name>|use <name>|clear]"
        skill = get_builtin_skill(parts[2].strip())
        if skill is None:
            return True, f"Unknown skill '{parts[2].strip()}'. Available: {', '.join(available_names)}"
        return True, (
            f"Skill {skill.name}: "
            f"mode={skill.max_mode} | "
            f"provider={skill.preferred_provider or 'unspecified'} | "
            f"model={skill.preferred_model or 'unspecified'} | "
            f"tools={len(skill.allowed_tools)}"
        )

    if sub == "use":
        if len(parts) < 3 or not parts[2].strip():
            return True, "Usage: /skills [list|show <name>|use <name>|clear]"
        skill = get_builtin_skill(parts[2].strip())
        if skill is None:
            return True, f"Unknown skill '{parts[2].strip()}'. Available: {', '.join(available_names)}"
        cfg = getattr(agent, "config", None)
        base_profile = _skill_base_profile_name(agent)
        profile_name = ensure_session_skill_profile(
            cfg,
            skill_name=skill.name,
            base_profile_name=base_profile,
        )
        _set_agent_policy_profile(agent, profile_name)
        setattr(agent, "_skills_base_profile", base_profile)
        setattr(agent, "_skills_active_name", skill.name)
        return True, f"Skill set to: {skill.name}"

    if sub == "clear":
        _clear_session_skill(agent)
        return True, "Skill cleared"

    return True, "Usage: /skills [list|show <name>|use <name>|clear]"


def handle_plugins_command(agent, text: str) -> tuple[bool, str]:
    """Handle `/plugins` command (list/show)."""
    raw = (text or "").strip()
    parts = raw.split()
    if not parts or parts[0].lower() != "/plugins":
        return False, ""

    cfg = getattr(agent, "config", None)
    sub = parts[1].strip().lower() if len(parts) > 1 else "list"
    plugins = _plugin_rows(cfg)
    names = [row["name"] for row in plugins]
    enabled = [row["name"] for row in plugins if row["enabled"]]

    if sub in {"list", "status"}:
        enabled_label = ", ".join(enabled) if enabled else "none"
        available_label = ", ".join(names) if names else "none"
        return True, f"Plugins: enabled={enabled_label} | available={available_label}"

    if sub == "show":
        if len(parts) < 3 or not parts[2].strip():
            return True, "Usage: /plugins [list|show <name>]"
        plugin_name = parts[2].strip()
        row = next((item for item in plugins if item["name"] == plugin_name), None)
        if row is None:
            return True, f"Unknown plugin '{plugin_name}'. Available: {', '.join(names)}"
        if row["type"] == "mcp":
            return True, (
                f"Plugin {row['name']}: type=mcp | enabled={_on_off(row['enabled'])} | "
                f"mode={row['mode']} | transport={row['transport']}"
            )
        return True, (
            f"Plugin {row['name']}: type=native | enabled={_on_off(row['enabled'])} | "
            f"source={row['source']}"
        )

    return True, "Usage: /plugins [list|show <name>]"


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


def handle_approvals_command(agent, text: str) -> tuple[bool, str]:
    """Handle `/approvals` command (status/on/off)."""
    raw = (text or "").strip()
    parts = raw.split()
    if not parts or parts[0].lower() != "/approvals":
        return False, ""

    if len(parts) == 1:
        sub = "status"
    elif len(parts) == 2 and parts[1].strip().lower() in {"status", "on", "off"}:
        sub = parts[1].strip().lower()
    else:
        return True, "Usage: /approvals [status|on|off]"

    if sub in {"on", "off"}:
        setter = getattr(agent, "set_terminal_approval_mode", None)
        if callable(setter):
            result = setter(sub == "on")
            if isinstance(result, str) and result.strip():
                return True, result
        return True, f"Approvals: requested={sub} | state=unavailable"

    getter = getattr(agent, "get_terminal_approval_status", None)
    if callable(getter):
        status = getter() or {}
        mode = "on" if bool(status.get("dangerous_mode", False)) else "off"
        pending = str(status.get("pending") or status.get("pending_command_preview") or "none").strip() or "none"
        approve_next_tokens = max(0, int(status.get("approve_next_tokens", 0) or 0))
        return True, (
            f"Approvals: dangerous_mode={mode} | "
            f"pending={pending} | "
            f"approve_next_tokens={approve_next_tokens}"
        )

    return True, "Approvals: dangerous_mode=off | pending=none | approve_next_tokens=0"


def handle_approve_command(agent, text: str) -> tuple[bool, str]:
    """Handle `/approve` command."""
    parts = (text or "").strip().split()
    if not parts or parts[0].lower() != "/approve":
        return False, ""
    if len(parts) != 1:
        return True, "Usage: /approve"

    approver = getattr(agent, "approve_pending_request", None)
    if callable(approver):
        result = approver()
        if isinstance(result, str) and result.strip():
            return True, result

    return True, "No pending dangerous request to approve."


def handle_deny_command(agent, text: str) -> tuple[bool, str]:
    """Handle `/deny` command."""
    parts = (text or "").strip().split()
    if not parts or parts[0].lower() != "/deny":
        return False, ""
    if len(parts) != 1:
        return True, "Usage: /deny"

    denier = getattr(agent, "deny_pending_request", None)
    if callable(denier):
        result = denier()
        if isinstance(result, str) and result.strip():
            return True, result

    return True, "No pending dangerous request to deny."


def handle_approve_next_command(agent, text: str) -> tuple[bool, str]:
    """Handle `/approve_next` command."""
    parts = (text or "").strip().split()
    if not parts or parts[0].lower() != "/approve_next":
        return False, ""
    if len(parts) != 1:
        return True, "Usage: /approve_next"

    approver = getattr(agent, "approve_next_dangerous_action", None)
    if callable(approver):
        result = approver()
        if isinstance(result, str) and result.strip():
            return True, result
        return True, "Approved next dangerous action."

    return True, "Approve-next unavailable: session approval state not wired."


def handle_profile_command(agent, text: str) -> tuple[bool, str]:
    """Handle `/profile` command (show/set policy profile)."""
    raw = (text or "").strip()
    parts = raw.split()
    if not parts or parts[0].lower() != "/profile":
        return False, ""

    sub = parts[1].strip().lower() if len(parts) > 1 else "show"
    cfg_profiles = getattr(getattr(agent, "config", None), "profiles", {}) or {}
    available = (
        [
            str(name)
            for name in cfg_profiles.keys()
            if not is_session_skill_profile_name(str(name))
        ]
        if isinstance(cfg_profiles, dict)
        else []
    )
    if not available:
        available = ["default"]

    if sub in {"show", "status", "list"}:
        active, _resolved_name, profile, _profile_missing = _resolve_profile_diagnostics(
            agent,
            getattr(agent, "config", None),
        )
        skill_suffix = f" | skill: {profile.skill_name}" if getattr(profile, "skill_name", "") else ""
        return True, f"Policy profile: {active}{skill_suffix} | available: {', '.join(available)}"

    if sub == "set":
        if len(parts) < 3 or not parts[2].strip():
            return True, "Usage: /profile [show|set <name>]"
        profile_name = parts[2].strip()
        if profile_name not in available:
            return True, f"Unknown profile '{profile_name}'. Available: {', '.join(available)}"
        _clear_session_skill(agent)
        setter = getattr(agent, "set_policy_profile", None)
        if callable(setter):
            setter(profile_name)
        else:
            setattr(agent, "policy_profile", profile_name)
        return True, f"Policy profile set to: {profile_name}"

    return True, "Usage: /profile [show|set <name>]"


def handle_mcp_command(agent, text: str) -> tuple[bool, str]:
    """Handle `/mcp` command (summary/servers/tools)."""
    raw = (text or "").strip()
    parts = raw.split()
    if not parts or parts[0].lower() != "/mcp":
        return False, ""

    cfg = getattr(agent, "config", None)
    mcp_cfg = getattr(cfg, "mcp", None)
    if mcp_cfg is None:
        return True, "MCP unavailable: missing config.mcp"

    servers = getattr(mcp_cfg, "servers", {}) or {}
    sub = parts[1].strip().lower() if len(parts) > 1 else "help"
    if sub in {"help", "status"}:
        enabled_names = sorted(
            name for name, server in servers.items() if bool(getattr(server, "enabled", False))
        )
        enabled_summary = ",".join(enabled_names) if enabled_names else "none"
        return True, (
            f"MCP: enabled={len(enabled_names)}/{len(servers)} | "
            f"servers={enabled_summary} | "
            "commands: /mcp servers, /mcp show <server>, /mcp tools <server>"
        )

    if sub == "servers":
        if not servers:
            return True, "MCP servers: none configured"
        lines = ["MCP servers:"]
        for name in sorted(servers):
            server = servers[name]
            enabled = "enabled" if bool(getattr(server, "enabled", False)) else "disabled"
            mode = str(getattr(server, "mode", "") or "").strip() or "unknown"
            transport = str(getattr(server, "transport", "") or "").strip() or "unknown"
            command = list(getattr(server, "command", []) or [])
            command_name = command[0] if command else "none"
            lines.append(
                f"- {name}: {enabled} | mode={mode} | transport={transport} | command={command_name}"
            )
        return True, "\n".join(lines)

    if sub == "show":
        if len(parts) < 3 or not parts[2].strip():
            return True, "Usage: /mcp show <server>"
        server_name = parts[2].strip().lower()
        server = servers.get(server_name)
        if server is None:
            return True, f"MCP server not found: {server_name}"
        enabled = "on" if bool(getattr(server, "enabled", False)) else "off"
        mode = str(getattr(server, "mode", "") or "").strip() or "unknown"
        transport = str(getattr(server, "transport", "") or "").strip() or "unknown"
        command = " ".join(str(part).strip() for part in (getattr(server, "command", []) or []) if str(part).strip()) or "(none)"
        env_keys = sorted((getattr(server, "env", {}) or {}).keys())
        env_summary = ", ".join(env_keys) if env_keys else "(none)"
        return True, "\n".join(
            [
                f"MCP server: {server_name}",
                f"enabled: {enabled}",
                f"mode: {mode}",
                f"transport: {transport}",
                f"command: {command}",
                f"env_keys: {env_summary}",
            ]
        )

    if sub == "tools":
        if len(parts) < 3 or not parts[2].strip():
            return True, "Usage: /mcp tools <server>"
        server_name = parts[2].strip()
        result = MCPClient(mcp_cfg).list_tools(server_name)
        if result.get("error"):
            return True, str(result["error"])
        tools = result.get("tools", [])
        if not tools:
            return True, f"MCP tools for {server_name}: none"
        lines = [f"MCP tools for {result.get('server', server_name)}:"]
        for item in tools:
            name = str(item.get("name", "") or "").strip()
            description = str(item.get("description", "") or "").strip()
            if description:
                lines.append(f"- {name}: {description}")
            else:
                lines.append(f"- {name}")
        return True, "\n".join(lines)

    return True, "Usage: /mcp [servers|show <server>|tools <server>]"


def _collect_job_summaries(limit: int = 10):
    max_items = max(1, int(limit))
    jobs = (
        list_worker_job_summaries(limit=max_items)
        + list_call_job_summaries(limit=max_items)
        + list_research_job_summaries(limit=max_items)
    )
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
    if ref.startswith("research:"):
        return load_research_job_summary(ref.split(":", 1)[1])
    job = load_worker_job_summary(ref)
    if job is not None:
        return job
    job = load_call_job_summary(ref)
    if job is not None:
        return job
    return load_research_job_summary(ref)


_ACTIVE_JOB_STATUSES = {
    "approved",
    "in_progress",
    "pending",
    "paused",
    "queued",
    "running",
    "starting",
    "waiting_approval",
}


def _job_is_active(job) -> bool:
    return str(getattr(job, "status", "") or "").strip().lower() in _ACTIVE_JOB_STATUSES


def _make_deep_research_refresh_client(agent):
    creator = getattr(agent, "_create_google_deep_research_client", None)
    if callable(creator):
        try:
            return creator()
        except Exception:
            return None

    cfg = getattr(agent, "config", None)
    if cfg is None:
        return None
    deep_cfg = getattr(getattr(cfg, "research", None), "google_deep_research", None)
    if deep_cfg is None or not bool(getattr(deep_cfg, "enabled", False)):
        return None

    llm_cfg = getattr(cfg, "llm", None)
    api_key = ""
    if str(getattr(llm_cfg, "provider", "") or "").strip().lower() == "google":
        api_key = str(getattr(llm_cfg, "api_key", "") or "").strip()
    if not api_key and str(getattr(llm_cfg, "fallback_provider", "") or "").strip().lower() == "google":
        api_key = str(getattr(llm_cfg, "fallback_api_key", "") or "").strip()
    if not api_key:
        api_key = str(os.environ.get("GEMINI_API_KEY", "")).strip()
    if not api_key:
        api_key = str(os.environ.get("GOOGLE_API_KEY", "")).strip()
    if not api_key:
        return None

    try:
        from archon.research.google_deep_research import GoogleDeepResearchClient

        return GoogleDeepResearchClient.from_api_key(
            api_key,
            agent=str(getattr(deep_cfg, "agent", "") or "").strip(),
        )
    except Exception:
        return None


def _refresh_job_summary(job, refresh_client):
    if refresh_client is None:
        return job
    if str(getattr(job, "kind", "") or "").strip().lower() != "deep_research":
        return job
    job_id = str(getattr(job, "job_id", "") or "").strip()
    if not job_id.startswith("research:"):
        return job
    refreshed = load_research_job_summary(job_id.split(":", 1)[1], refresh_client=refresh_client)
    return refreshed or job


def _parse_jobs_args(parts: list[str]) -> tuple[str, int] | None:
    view = "all"
    limit = 10
    for token in parts[1:]:
        value = str(token or "").strip()
        if not value:
            continue
        lowered = value.lower()
        if lowered in {"all", "active"}:
            if view != "all":
                return None
            view = lowered
            continue
        try:
            limit = int(value)
        except ValueError:
            return None
    return view, max(1, limit)


def _format_jobs_list(jobs, *, view: str, limit: int) -> str:
    scan_limit = max(limit, 100) if view == "active" else limit
    recent_jobs = list(jobs[:scan_limit])
    active_count = sum(1 for job in recent_jobs if _job_is_active(job))
    if view == "active":
        selected = [job for job in recent_jobs if _job_is_active(job)][:limit]
        if not selected:
            return "Jobs: none active"
        header = f"Jobs: showing={len(selected)} | active={active_count} | filter=active"
        return header + "\n" + format_job_summary_list(selected)
    if not recent_jobs:
        return "Jobs: none"
    header = f"Jobs: showing={len(recent_jobs)} | active={active_count}"
    return header + "\n" + format_job_summary_list(recent_jobs)


def handle_jobs_command(agent, text: str) -> tuple[bool, str]:
    """Handle `/jobs` command (list recent cross-surface jobs)."""
    raw = (text or "").strip()
    parts = raw.split()
    if not parts or parts[0].lower() != "/jobs":
        return False, ""

    # /jobs purge [statuses...]
    if len(parts) >= 2 and parts[1].lower() == "purge":
        statuses = [s.lower() for s in parts[2:]] if len(parts) > 2 else None
        research_removed = purge_completed_jobs(statuses=statuses)
        worker_removed = purge_stale_sessions(statuses=statuses)
        total = research_removed + worker_removed
        if total == 0:
            return True, "No jobs to purge."
        details = []
        if research_removed:
            details.append(f"{research_removed} research")
        if worker_removed:
            details.append(f"{worker_removed} worker")
        return True, f"Purged {total} local records ({', '.join(details)})."

    parsed = _parse_jobs_args(parts)
    if parsed is None:
        return True, "Usage: /jobs [active|all|purge] [limit]"
    view, limit = parsed

    scan_limit = max(limit, 100) if view == "active" else limit
    try:
        jobs = _collect_job_summaries(limit=scan_limit)
        refresh_client = None
        if any(
            str(getattr(job, "kind", "") or "").strip().lower() == "deep_research"
            for job in jobs
        ):
            refresh_client = _make_deep_research_refresh_client(agent)
        if refresh_client is not None:
            jobs = [_refresh_job_summary(job, refresh_client) for job in jobs]
    except OSError as e:
        return True, f"Jobs unavailable: {e}"
    return True, _format_jobs_list(jobs, view=view, limit=limit)


def handle_job_command(agent, text: str) -> tuple[bool, str]:
    """Handle `/job <id>` command (show one normalized job summary)."""
    raw = (text or "").strip()
    parts = raw.split(maxsplit=2)
    if not parts or parts[0].lower() != "/job":
        return False, ""
    if len(parts) < 2 or not parts[1].strip():
        return True, "Usage: /job <id>"

    # /job cancel <id>
    if parts[1].strip().lower() == "cancel":
        if len(parts) < 3 or not parts[2].strip():
            return True, "Usage: /job cancel <research:id>"
        cancel_ref = parts[2].strip()
        if not cancel_ref.startswith("research:"):
            return True, "Only research jobs can be cancelled (use research:<id>)."
        interaction_id = cancel_ref.split(":", 1)[1]
        remote_error = ""
        remote_cancelled = False
        refresh_client = _make_deep_research_refresh_client(agent)
        if refresh_client is not None:
            cancel_fn = getattr(refresh_client, "cancel_research", None)
            if callable(cancel_fn):
                try:
                    remote_result = cancel_fn(interaction_id)
                    remote_status = str(getattr(remote_result, "status", "") or "").strip().lower()
                    remote_cancelled = remote_status in {"cancelled", "canceled", "canceling"}
                except Exception as e:
                    remote_error = f"{type(e).__name__}: {e}"
        result = cancel_research_job(interaction_id, reason="Cancelled by user")
        if result is None:
            return True, f"Job not found: {cancel_ref}"
        if result.status != "cancelled":
            return True, f"Job already in terminal state: {result.status}"
        if remote_cancelled:
            return True, f"Cancelled {cancel_ref} remotely and locally."
        if remote_error:
            return (
                True,
                f"Marked local record cancelled for {cancel_ref}. "
                f"Remote cancellation failed: {remote_error}",
            )
        return (
            True,
            f"Marked local record cancelled for {cancel_ref}. "
            "Remote Deep Research may still continue if the provider does not support cancellation.",
        )

    job_ref = parts[1].strip()
    try:
        if job_ref.startswith("research:"):
            interaction_id = job_ref.split(":", 1)[1]
            refresh_client = _make_deep_research_refresh_client(agent)
            job = load_research_job(
                interaction_id,
                refresh_client=refresh_client,
                hook_bus=getattr(agent, "hooks", None),
            )
            if job is not None and not _research_status_terminal(getattr(job, "status", "")) and refresh_client is not None:
                poll_interval = int(
                    getattr(
                        getattr(getattr(getattr(agent, "config", None), "research", None), "google_deep_research", None),
                        "poll_interval_sec",
                        10,
                    )
                    or 10
                )
                start_research_job_monitor(
                    interaction_id,
                    refresh_client=refresh_client,
                    poll_interval_sec=poll_interval,
                    hook_bus=getattr(agent, "hooks", None),
                )
        else:
            job = _load_job_summary(job_ref)
    except OSError as e:
        return True, f"Job unavailable: {e}"
    if job is None:
        return True, f"Job not found: {job_ref}"
    if job_ref.startswith("research:"):
        return True, _format_research_job_record(job, cfg=getattr(agent, "config", None))
    return True, format_job_summary(job)


def _describe_orchestrator_mode(cfg) -> str:
    orchestrator = getattr(cfg, "orchestrator", None)
    if orchestrator is None:
        return "legacy"
    enabled = bool(getattr(orchestrator, "enabled", False))
    mode = str(getattr(orchestrator, "mode", "legacy") or "legacy").strip().lower() or "legacy"
    if not enabled:
        return "legacy"
    if mode == "hybrid":
        return "hybrid(route-only)"
    return mode


def _count_enabled_mcp_servers(cfg) -> int:
    servers = getattr(getattr(cfg, "mcp", None), "servers", {}) or {}
    return sum(1 for server in servers.values() if bool(getattr(server, "enabled", False)))


def _format_mcp_counts(cfg) -> str:
    servers = getattr(getattr(cfg, "mcp", None), "servers", {}) or {}
    enabled = _count_enabled_mcp_servers(cfg)
    return f"{enabled}/{len(servers)}"


def _format_research_job_record(record, *, cfg=None) -> str:
    interaction_id = str(getattr(record, "interaction_id", "") or "").strip()
    status = str(getattr(record, "status", "") or "unknown").strip() or "unknown"
    summary = str(getattr(record, "summary", "") or "").strip() or "unknown"
    updated_at = str(getattr(record, "updated_at", "") or "").strip()
    provider_status = str(getattr(record, "provider_status", "") or status).strip() or status
    last_polled_at = str(getattr(record, "last_polled_at", "") or "").strip() or "(not yet refreshed)"
    poll_count = max(0, int(getattr(record, "poll_count", 0) or 0))
    created_at = str(getattr(record, "created_at", "") or "").strip()
    created_at_dt = _parse_iso_datetime(created_at)
    last_polled_dt = _parse_iso_datetime(last_polled_at if last_polled_at != "(not yet refreshed)" else "")
    refresh_attempted = bool(getattr(record, "_refresh_attempted", False))
    refresh_ok = bool(getattr(record, "_refresh_ok", False))
    refresh_error = str(getattr(record, "_refresh_error", "") or "").strip()
    timeout_minutes = max(
        1,
        int(
            getattr(record, "timeout_minutes", 0)
            or getattr(
                getattr(getattr(cfg, "research", None), "google_deep_research", None),
                "timeout_minutes",
                20,
            )
            or 20
        ),
    )
    poll_interval = max(
        1,
        int(
            getattr(
                getattr(getattr(cfg, "research", None), "google_deep_research", None),
                "poll_interval_sec",
                10,
            )
            or 10
        ),
    )
    lines = [
        f"job_id: research:{interaction_id}",
        "job_kind: deep_research",
        f"job_status: {status}",
        f"job_summary: {summary}",
        f"job_last_update_at: {updated_at}",
        f"job_provider_status: {provider_status}",
        f"job_last_polled_at: {last_polled_at}",
        f"job_elapsed: {_format_elapsed(created_at)}",
        f"job_poll_count: {poll_count}",
        f"job_live_status: {_format_research_live_status(status, refresh_attempted, refresh_ok, refresh_error, last_polled_dt, created_at_dt, timeout_minutes)}",
        f"job_refresh_age: {_format_refresh_age(last_polled_dt)}",
        f"job_next_poll_due_in: {_format_next_poll_due(status, last_polled_dt, poll_interval)}",
    ]
    if refresh_attempted and not refresh_ok and refresh_error:
        lines.append(f"job_last_refresh_error: {refresh_error}")
    output_text = str(getattr(record, "output_text", "") or "").strip()
    if output_text:
        lines.append("job_output_preview:")
        lines.extend(output_text[:1000].splitlines()[:10] or [output_text[:1000]])
    error = str(getattr(record, "error", "") or "").strip()
    if error:
        lines.append(f"job_error: {error}")
    return "\n".join(lines)


def _format_elapsed(started_at: str) -> str:
    started = _parse_iso_datetime(started_at)
    if started is None:
        return "unknown"
    delta = max(0, int((datetime.now(timezone.utc) - started).total_seconds()))
    if delta < 60:
        return f"{delta}s"
    if delta < 3600:
        minutes, seconds = divmod(delta, 60)
        return f"{minutes}m {seconds}s"
    hours, rem = divmod(delta, 3600)
    minutes = rem // 60
    return f"{hours}h {minutes}m"


def _format_refresh_age(last_polled_at: datetime | None) -> str:
    if last_polled_at is None:
        return "(not yet polled)"
    delta = max(0, int((datetime.now(timezone.utc) - last_polled_at).total_seconds()))
    if delta < 60:
        return f"{delta}s"
    if delta < 3600:
        minutes, seconds = divmod(delta, 60)
        return f"{minutes}m {seconds}s"
    hours, rem = divmod(delta, 3600)
    minutes = rem // 60
    return f"{hours}h {minutes}m"


def _format_next_poll_due(status: str, last_polled_at: datetime | None, poll_interval: int) -> str:
    if _research_status_terminal(status):
        return "n/a"
    if last_polled_at is None:
        return "now"
    age = max(0, int((datetime.now(timezone.utc) - last_polled_at).total_seconds()))
    remaining = max(0, int(poll_interval) - age)
    return f"{remaining}s"


def _format_research_live_status(
    status: str,
    refresh_attempted: bool,
    refresh_ok: bool,
    refresh_error: str,
    last_polled_at: datetime | None,
    created_at: datetime | None,
    timeout_minutes: int,
) -> str:
    normalized = str(status or "").strip().lower()
    if refresh_attempted:
        if refresh_ok:
            if normalized in {"in_progress", "running", "queued", "starting"}:
                if _research_runtime_exceeds_timeout(created_at, timeout_minutes):
                    return f"remote reachable | running longer than configured {timeout_minutes}m timeout"
                return "remote reachable | running normally"
            if normalized == "requires_action":
                return "remote reachable | action required"
            if _research_status_terminal(normalized):
                return "remote reachable | terminal state confirmed"
            return f"remote reachable | {normalized or 'unknown'}"
        if last_polled_at is not None:
            return "cached status | last remote check failed"
        return f"last remote check failed | {refresh_error or 'unknown error'}"
    if last_polled_at is not None:
        return "using cached status"
    return "waiting for first successful poll"


def _research_runtime_exceeds_timeout(created_at: datetime | None, timeout_minutes: int) -> bool:
    if created_at is None:
        return False
    elapsed_seconds = max(0, int((datetime.now(timezone.utc) - created_at).total_seconds()))
    return elapsed_seconds > max(1, int(timeout_minutes or 20)) * 60


def _parse_iso_datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _research_status_terminal(status: str) -> bool:
    return str(status or "").strip().lower() in {"completed", "done", "failed", "error", "cancelled"}


def _plugin_rows(cfg) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for name, source, enabled_fn in _NATIVE_PLUGIN_SPECS:
        rows.append(
            {
                "name": name,
                "type": "native",
                "enabled": bool(enabled_fn(cfg)),
                "source": source,
            }
        )
    servers = getattr(getattr(cfg, "mcp", None), "servers", {}) or {}
    for server_name, server in servers.items():
        rows.append(
            {
                "name": f"mcp:{server_name}",
                "type": "mcp",
                "enabled": bool(getattr(server, "enabled", False)),
                "mode": str(getattr(server, "mode", "") or "unknown").strip() or "unknown",
                "transport": str(getattr(server, "transport", "") or "unknown").strip() or "unknown",
            }
        )
    return rows


def _on_off(enabled: object) -> str:
    return "on" if bool(enabled) else "off"


def _active_skill_name(agent) -> str:
    cfg = getattr(agent, "config", None)
    _display_name, _resolved_name, profile, _missing = _resolve_profile_diagnostics(agent, cfg)
    skill_name = str(getattr(profile, "skill_name", "") or "").strip().lower()
    return skill_name


def _skill_base_profile_name(agent) -> str:
    base_profile = str(getattr(agent, "_skills_base_profile", "") or "").strip()
    if base_profile:
        return base_profile
    active_profile = str(getattr(agent, "policy_profile", "") or "").strip() or "default"
    if is_session_skill_profile_name(active_profile):
        return "default"
    return active_profile


def _set_agent_policy_profile(agent, profile_name: str) -> None:
    setter = getattr(agent, "set_policy_profile", None)
    if callable(setter):
        setter(profile_name)
    else:
        setattr(agent, "policy_profile", profile_name)


def _clear_session_skill(agent) -> None:
    cfg = getattr(agent, "config", None)
    profiles = getattr(cfg, "profiles", None)
    current_profile = str(getattr(agent, "policy_profile", "") or "").strip()
    base_profile = str(getattr(agent, "_skills_base_profile", "") or "").strip() or "default"
    active_session_skill = is_session_skill_profile_name(current_profile)
    if isinstance(profiles, dict) and active_session_skill:
        profiles.pop(current_profile, None)
    if active_session_skill or hasattr(agent, "_skills_base_profile"):
        _set_agent_policy_profile(agent, base_profile)
    if hasattr(agent, "_skills_base_profile"):
        delattr(agent, "_skills_base_profile")
    if hasattr(agent, "_skills_active_name"):
        delattr(agent, "_skills_active_name")


def _maybe_auto_activate_skill(agent, text: str) -> tuple[bool, str]:
    """Auto-activate a built-in skill from an explicit natural-language request."""
    skill_name = _extract_explicit_skill_request(text)
    if not skill_name:
        return False, ""
    if _active_skill_name(agent) == skill_name:
        return False, ""
    cfg = getattr(agent, "config", None)
    base_profile = _skill_base_profile_name(agent)
    profile_name = ensure_session_skill_profile(
        cfg,
        skill_name=skill_name,
        base_profile_name=base_profile,
    )
    _set_agent_policy_profile(agent, profile_name)
    setattr(agent, "_skills_base_profile", base_profile)
    setattr(agent, "_skills_active_name", skill_name)
    return True, f"Skill auto-activated: {skill_name}"


def _extract_explicit_skill_request(text: str) -> str:
    compact = " ".join(str(text or "").split())
    if not compact or compact.startswith("/"):
        return ""
    for pattern in _EXPLICIT_SKILL_PATTERNS:
        match = pattern.search(compact)
        if not match:
            continue
        requested = str(match.group("skill") or "").strip().lower()
        return _SKILL_REQUEST_ALIASES.get(requested, "")
    return ""


def _llm_runtime_ready(agent, cfg) -> bool:
    llm = getattr(agent, "llm", None)
    if str(getattr(llm, "api_key", "") or "").strip():
        return True
    llm_cfg = getattr(cfg, "llm", None)
    if str(getattr(llm_cfg, "api_key", "") or "").strip():
        return True
    provider = str(getattr(llm, "provider", "") or getattr(llm_cfg, "provider", "") or "").strip().lower()
    env_name = {
        "google": "GEMINI_API_KEY",
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
    }.get(provider)
    return bool(env_name and str(os.environ.get(env_name, "")).strip())


def _resolve_profile_diagnostics(agent, cfg):
    requested_name = str(getattr(agent, "policy_profile", "") or "").strip() or "default"
    profiles = getattr(cfg, "profiles", {}) or {}
    if is_session_skill_profile_name(requested_name):
        base_name = str(getattr(agent, "_skills_base_profile", "") or "").strip() or "default"
        resolved_name, profile = resolve_profile(cfg, profile_name=requested_name)
        return base_name, requested_name, profile, False
    profile_exists = isinstance(profiles, dict) and requested_name in profiles
    resolved_name, profile = resolve_profile(cfg, profile_name=requested_name)
    if profile_exists or requested_name == resolved_name:
        display_name = resolved_name
        missing = False
    else:
        display_name = f"{requested_name}->{resolved_name}"
        missing = True
    return display_name, resolved_name, profile, missing


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
        return ("help", _TERMINAL_HELP_TEXT)
    handled, msg = handle_status_command(agent, raw)
    if handled:
        return "status", msg
    handled, msg = handle_cost_command(agent, raw)
    if handled:
        return "cost", msg
    handled, msg = handle_compact_command(agent, raw)
    if handled:
        return "compact", msg
    handled, msg = handle_clear_command(agent, raw)
    if handled:
        return "clear", msg
    handled, msg = handle_context_command(agent, raw)
    if handled:
        return "context", msg
    handled, msg = handle_doctor_command(agent, raw)
    if handled:
        return "doctor", msg
    handled, msg = handle_permissions_command(agent, raw)
    if handled:
        return "permissions", msg
    handled, msg = handle_approvals_command(agent, raw)
    if handled:
        return "approvals", msg
    handled, msg = handle_approve_command(agent, raw)
    if handled:
        return "approve", msg
    handled, msg = handle_deny_command(agent, raw)
    if handled:
        return "deny", msg
    handled, msg = handle_approve_next_command(agent, raw)
    if handled:
        return "approve_next", msg
    handled, msg = handle_skills_command(agent, raw)
    if handled:
        return "skills", msg
    handled, msg = handle_plugins_command(agent, raw)
    if handled:
        return "plugins", msg
    handled, msg = handle_mcp_command(agent, raw)
    if handled:
        return "mcp", msg
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
