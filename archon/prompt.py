"""System prompt assembly from templates and runtime data."""

import json
import subprocess
from pathlib import Path

from archon.config import Config, ProfileConfig
from archon.control.skills import (
    build_skill_guidance as build_profile_skill_guidance,
)
from archon.control.policy import evaluate_mcp_policy, resolve_profile
from archon.system import get_profile, format_profile
from archon.introspect import format_self_awareness
from archon.memory import summary as memory_summary


PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
AGENT_CONTEXT_PATH = Path(__file__).parent.parent / "AGENT_CONTEXT.json"


def load_template(name: str) -> str:
    """Load a prompt template from the prompts/ directory."""
    path = PROMPTS_DIR / name
    if not path.exists():
        return ""
    return path.read_text()


def build_system_prompt(tool_count: int = 7) -> str:
    """Assemble the full system prompt from template + runtime data."""
    template = load_template("system.md")
    guidelines = load_template("guidelines.md")

    profile = get_profile()
    system_profile = format_profile(profile)
    self_awareness = format_self_awareness()
    mem_summary = memory_summary()

    if mem_summary:
        mem_section = f"Your persistent memory:\n```\n{mem_summary}\n```"
    else:
        mem_section = "No persistent memory yet. Use memory_write to save important context."

    prompt = template.format(
        system_profile=system_profile,
        tool_count=tool_count,
        self_awareness=self_awareness,
        memory_summary=mem_section,
        guidelines=guidelines,
    )

    return prompt.strip()


def build_skill_guidance(config: Config, profile_name: str = "default") -> str:
    """Render minimal skill guidance for a selected non-default built-in skill."""
    key = (profile_name or "default").strip() or "default"
    profile = config.profiles.get(key)
    if profile is None:
        profile = config.profiles.get("default", ProfileConfig())
    return build_profile_skill_guidance(profile)


def build_runtime_capability_summary(config: Config, profile_name: str = "default") -> str:
    """Render current session-effective capability state for grounding capability answers."""
    resolved_name, profile = resolve_profile(config, profile_name=profile_name)
    active_skill = profile.skill_name or "none"
    if "*" in profile.allowed_tools:
        tool_scope = "all tools"
    else:
        tool_scope = f"{len(profile.allowed_tools)} tools"
    servers = getattr(getattr(config, "mcp", None), "servers", {}) or {}
    enabled_servers = [
        str(name).strip().lower()
        for name, server in servers.items()
        if str(name).strip() and bool(getattr(server, "enabled", False))
    ]
    allowed_servers = sorted(
        server_name
        for server_name in enabled_servers
        if evaluate_mcp_policy(
            config=config,
            server_name=server_name,
            profile_name=resolved_name,
        ).decision == "allow"
    )
    enabled_label = ", ".join(enabled_servers) if enabled_servers else "none"
    allowed_label = ", ".join(allowed_servers) if allowed_servers else "none"
    lines = [
        "[Runtime Capabilities]",
        f"Active policy profile: {resolved_name}",
        f"Active skill: {active_skill}",
        f"Effective tool scope: {tool_scope}",
        f"Enabled MCP servers: {allowed_label}",
    ]
    if profile.preferred_provider or profile.preferred_model:
        lines.append(
            "Skill model hint: "
            f"{profile.preferred_provider or 'unspecified'} / "
            f"{profile.preferred_model or 'unspecified'}"
        )
    if enabled_label != allowed_label:
        lines.append(f"Configured MCP servers: {enabled_label}")
    return "\n".join(lines)


def _git_output(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=Path(__file__).parent.parent,
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return ""
    return str(result.stdout or "").strip()


def build_source_awareness_summary() -> str:
    """Render a compact source-of-truth summary for capability grounding."""
    lines: list[str] = ["[Source Awareness]"]

    branch = _git_output("rev-parse", "--abbrev-ref", "HEAD")
    if branch:
        lines.append(f"Git branch: {branch}")
    head = _git_output("rev-parse", "--short", "HEAD")
    if head:
        lines.append(f"Git head: {head}")

    try:
        payload = json.loads(AGENT_CONTEXT_PATH.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    project = str(payload.get("project", "") or "").strip()
    version = str(payload.get("version", "") or "").strip()
    if project and version:
        lines.append(f"Project: {project} v{version}")
    elif project:
        lines.append(f"Project: {project}")

    total_tests = payload.get("total_tests")
    if isinstance(total_tests, int) and total_tests > 0:
        lines.append(f"Verified tests: {total_tests}")

    changelog = payload.get("changelog")
    recent_changes = []
    if isinstance(changelog, list):
        recent_changes = [str(item).strip() for item in changelog if str(item).strip()][-2:]
    if recent_changes:
        lines.append("Recent changes:")
        lines.extend(f"- {item}" for item in recent_changes)

    return "\n".join(lines) if len(lines) > 1 else ""
