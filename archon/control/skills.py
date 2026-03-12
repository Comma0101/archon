"""Built-in skill registry and profile default resolution."""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from archon.config import ProfileConfig
    from archon.skills.loader import MarkdownSkill


DEFAULT_SKILL_NAME = "general"
SESSION_SKILL_PROFILE_PREFIX = "__skill__:"
_DEFAULT_ALLOWED_TOOLS = ("*",)
_DEFAULT_MAX_MODE = "implement"


@dataclass(frozen=True)
class BuiltinSkill:
    name: str
    allowed_tools: tuple[str, ...]
    max_mode: str = _DEFAULT_MAX_MODE
    preferred_provider: str = ""
    preferred_model: str = ""
    prompt_guidance: str = ""


@dataclass(frozen=True)
class ResolvedSkillProfile:
    skill_name: str = ""
    allowed_tools: tuple[str, ...] = _DEFAULT_ALLOWED_TOOLS
    max_mode: str = _DEFAULT_MAX_MODE
    execution_backend: str = "host"
    preferred_provider: str = ""
    preferred_model: str = ""
    prompt_guidance: str = ""


BUILTIN_SKILLS: dict[str, BuiltinSkill] = {
    "general": BuiltinSkill(
        name="general",
        allowed_tools=_DEFAULT_ALLOWED_TOOLS,
        preferred_provider="anthropic",
        preferred_model="claude-sonnet-4-6",
        prompt_guidance="Use the full default tool surface and adapt to the user's task.",
    ),
    "coder": BuiltinSkill(
        name="coder",
        allowed_tools=(
            "shell",
            "read_file",
            "write_file",
            "edit_file",
            "list_dir",
            "memory_read",
            "memory_write",
            "memory_lookup",
            "memory_inbox_add",
            "web_search",
            "web_read",
            "delegate_code_task",
            "worker_start",
            "worker_send",
            "worker_status",
            "worker_list",
            "worker_poll",
            "worker_cancel",
            "worker_approve",
            "worker_reconcile",
        ),
        preferred_provider="anthropic",
        preferred_model="claude-sonnet-4-6",
        prompt_guidance="Bias toward direct code inspection, minimal edits, and local verification.",
    ),
    "researcher": BuiltinSkill(
        name="researcher",
        allowed_tools=(
            "deep_research",
            "read_file",
            "list_dir",
            "memory_read",
            "memory_lookup",
            "memory_inbox_add",
            "news_brief",
            "web_search",
            "web_read",
        ),
        preferred_provider="openai",
        preferred_model="gpt-4o",
        prompt_guidance="Prioritize current-source gathering and source-grounded answers before conclusions.",
    ),
    "operator": BuiltinSkill(
        name="operator",
        allowed_tools=(
            "shell",
            "read_file",
            "list_dir",
            "memory_read",
            "memory_lookup",
            "voice_service_status",
            "voice_service_start",
            "voice_service_stop",
            "call_mission_start",
            "call_mission_status",
            "call_mission_list",
            "call_mission_cancel",
            "delegate_code_task",
            "worker_start",
            "worker_send",
            "worker_status",
            "worker_list",
            "worker_poll",
            "worker_cancel",
            "worker_approve",
            "worker_reconcile",
        ),
        preferred_provider="anthropic",
        preferred_model="claude-sonnet-4-6",
        prompt_guidance="Focus on status, execution control, and bounded operational changes.",
    ),
    "sales": BuiltinSkill(
        name="sales",
        allowed_tools=(
            "read_file",
            "list_dir",
            "memory_read",
            "memory_write",
            "memory_lookup",
            "memory_inbox_add",
            "web_search",
            "web_read",
            "call_mission_start",
            "call_mission_status",
            "call_mission_list",
            "call_mission_cancel",
            "voice_service_status",
        ),
        preferred_provider="openai",
        preferred_model="gpt-4o",
        prompt_guidance="Optimize for concise external messaging, product facts, and outreach context.",
    ),
    "memory_curator": BuiltinSkill(
        name="memory_curator",
        allowed_tools=(
            "read_file",
            "list_dir",
            "memory_read",
            "memory_write",
            "memory_lookup",
            "memory_inbox_add",
            "memory_inbox_list",
            "memory_inbox_decide",
        ),
        preferred_provider="anthropic",
        preferred_model="claude-sonnet-4-6",
        prompt_guidance="Be conservative with persistence: read first, deduplicate, then apply targeted memory updates.",
    ),
}


def get_builtin_skill(name: str | None) -> BuiltinSkill | None:
    key = str(name or "").strip().lower()
    if not key:
        return None
    return BUILTIN_SKILLS.get(key)


def list_builtin_skills() -> list[BuiltinSkill]:
    return list(BUILTIN_SKILLS.values())


def make_session_skill_profile_name(base_profile_name: str, skill_name: str) -> str:
    base = str(base_profile_name or "default").strip().lower() or "default"
    skill = str(skill_name or "").strip().lower()
    return f"{SESSION_SKILL_PROFILE_PREFIX}{base}:{skill}"


def is_session_skill_profile_name(profile_name: str | None) -> bool:
    return str(profile_name or "").startswith(SESSION_SKILL_PROFILE_PREFIX)


def ensure_session_skill_profile(config, *, skill_name: str, base_profile_name: str = "default") -> str:
    from archon.config import ProfileConfig

    skill = get_builtin_skill(skill_name)
    if skill is None:
        raise ValueError(f"Unknown skill '{skill_name}'")

    profiles = getattr(config, "profiles", None)
    if not isinstance(profiles, dict):
        config.profiles = {"default": ProfileConfig()}
        profiles = config.profiles

    base_name = str(base_profile_name or "default").strip() or "default"
    base_profile = profiles.get(base_name) or profiles.get("default") or ProfileConfig()
    profile_name = make_session_skill_profile_name(base_name, skill.name)
    profiles[profile_name] = ProfileConfig(
        allowed_tools=list(getattr(base_profile, "allowed_tools", _DEFAULT_ALLOWED_TOOLS)),
        max_mode=str(getattr(base_profile, "max_mode", _DEFAULT_MAX_MODE) or _DEFAULT_MAX_MODE),
        execution_backend=str(getattr(base_profile, "execution_backend", "host") or "host"),
        skill=skill.name,
    )
    return profile_name


def resolve_skill_profile(profile: ProfileConfig | None) -> ResolvedSkillProfile:
    profile = profile or _fallback_profile()
    allowed_tools = _normalize_allowed_tools(getattr(profile, "allowed_tools", _DEFAULT_ALLOWED_TOOLS))
    max_mode = _normalize_mode(getattr(profile, "max_mode", _DEFAULT_MAX_MODE))
    execution_backend = _normalize_backend(getattr(profile, "execution_backend", "host"))
    allowed_tools_explicit = bool(getattr(profile, "allowed_tools_explicit", False))
    max_mode_explicit = bool(getattr(profile, "max_mode_explicit", False))
    requested_skill_name = str(getattr(profile, "skill", "") or "").strip().lower()

    selected_skill = get_builtin_skill(requested_skill_name)
    if selected_skill is None:
        if requested_skill_name:
            if not allowed_tools_explicit:
                allowed_tools = ()
            return ResolvedSkillProfile(
                allowed_tools=allowed_tools,
                max_mode=max_mode,
                execution_backend=execution_backend,
            )
        return ResolvedSkillProfile(
            allowed_tools=allowed_tools,
            max_mode=max_mode,
            execution_backend=execution_backend,
        )

    if not allowed_tools_explicit:
        allowed_tools = selected_skill.allowed_tools
    if not max_mode_explicit:
        max_mode = selected_skill.max_mode

    return ResolvedSkillProfile(
        skill_name=selected_skill.name,
        allowed_tools=allowed_tools,
        max_mode=max_mode,
        execution_backend=execution_backend,
        preferred_provider=selected_skill.preferred_provider,
        preferred_model=selected_skill.preferred_model,
        prompt_guidance=selected_skill.prompt_guidance,
    )


def build_skill_guidance(profile: ProfileConfig | None) -> str:
    resolved = resolve_skill_profile(profile)
    if not resolved.skill_name or resolved.skill_name == DEFAULT_SKILL_NAME:
        return ""

    lines = [
        "[Skill Guidance]",
        f"Active skill: {resolved.skill_name}",
    ]
    if resolved.preferred_provider or resolved.preferred_model:
        lines.append(
            "Preferred model: "
            f"{resolved.preferred_provider or 'unspecified'} / "
            f"{resolved.preferred_model or 'unspecified'}"
        )
    if resolved.prompt_guidance:
        lines.append(resolved.prompt_guidance)
    return "\n".join(lines)


def _normalize_allowed_tools(values: object) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        return _DEFAULT_ALLOWED_TOOLS
    cleaned = tuple(str(item).strip() for item in values if str(item).strip())
    return cleaned or _DEFAULT_ALLOWED_TOOLS


def _normalize_mode(value: object) -> str:
    normalized = str(value or _DEFAULT_MAX_MODE).strip().lower()
    return normalized or _DEFAULT_MAX_MODE


def _normalize_backend(value: object) -> str:
    normalized = str(value or "host").strip().lower()
    return normalized or "host"


def _fallback_profile() -> ProfileConfig:
    from archon.config import ProfileConfig

    return ProfileConfig()


# --- Markdown skill support ---

from archon.config import SKILLS_DIR  # noqa: E402

_MARKDOWN_SKILLS_DIR = SKILLS_DIR


@functools.lru_cache(maxsize=1)
def _loaded_markdown_skills() -> dict[str, MarkdownSkill]:
    """Load and cache markdown skills from disk."""
    try:
        from archon.skills.loader import load_markdown_skills
        skills = load_markdown_skills(_MARKDOWN_SKILLS_DIR)
        return {s.name: s for s in skills}
    except Exception:
        return {}


def reload_markdown_skills() -> None:
    """Clear the cached markdown skills so they are reloaded from disk on next access."""
    _loaded_markdown_skills.cache_clear()


def find_markdown_skill_match(text: str) -> MarkdownSkill | None:
    """Match user text against markdown skill triggers."""
    lowered = str(text or "").strip().lower()
    if not lowered:
        return None
    for skill in _loaded_markdown_skills().values():
        for trigger in skill.triggers:
            if trigger and trigger.lower() in lowered:
                return skill
    return None


def ensure_markdown_session_skill_profile(config, *, skill_name: str, base_profile_name: str = "default") -> str:
    """Create a session profile for a markdown skill."""
    from archon.config import ProfileConfig

    skill = _loaded_markdown_skills().get(str(skill_name or "").strip())
    if skill is None:
        raise ValueError(f"Unknown markdown skill '{skill_name}'")
    profile_name = make_session_skill_profile_name(base_profile_name, skill.name)
    config.profiles[profile_name] = ProfileConfig(
        allowed_tools=skill.to_profile_kwargs()["allowed_tools"],
        max_mode="implement",
        execution_backend="host",
        skill=skill.name,
    )
    return profile_name
