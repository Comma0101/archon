"""Capability policy evaluation for control-plane shadow/enforcement modes."""

from __future__ import annotations

from dataclasses import dataclass

from archon.config import Config, ProfileConfig
from archon.control.skills import ResolvedSkillProfile, resolve_skill_profile


@dataclass
class PolicyDecision:
    decision: str  # allow | shadow_deny | deny
    reason: str
    profile: str
    tool_name: str
    mode: str


_MODE_RANK = {
    "analyze": 1,
    "review": 2,
    "implement": 3,
    "debug": 4,
}


def resolve_profile(
    config: Config,
    profile_name: str = "default",
) -> tuple[str, ResolvedSkillProfile]:
    name = (profile_name or "default").strip()
    if name in config.profiles:
        return name, resolve_skill_profile(config.profiles[name])
    return "default", resolve_skill_profile(config.profiles.get("default", ProfileConfig()))


def evaluate_tool_policy(
    *,
    config: Config,
    tool_name: str,
    mode: str = "implement",
    profile_name: str = "default",
) -> PolicyDecision:
    tool = (tool_name or "").strip()
    normalized_mode = (mode or "implement").strip().lower()
    profile_key, profile = resolve_profile(config, profile_name=profile_name)

    tool_allowed = _tool_is_allowed(profile, tool)
    mode_allowed = _mode_is_allowed(profile, normalized_mode)
    if tool_allowed and mode_allowed:
        return PolicyDecision(
            decision="allow",
            reason="allowed",
            profile=profile_key,
            tool_name=tool,
            mode=normalized_mode,
        )

    reasons = []
    if not tool_allowed:
        reasons.append("tool_not_allowed")
    if not mode_allowed:
        reasons.append("mode_exceeds_profile")
    reason = ",".join(reasons) or "denied"

    if _policy_enforced(config):
        return PolicyDecision(
            decision="deny",
            reason=reason,
            profile=profile_key,
            tool_name=tool,
            mode=normalized_mode,
        )
    return PolicyDecision(
        decision="shadow_deny",
        reason=reason,
        profile=profile_key,
        tool_name=tool,
        mode=normalized_mode,
    )


def _tool_is_allowed(profile: ResolvedSkillProfile, tool_name: str) -> bool:
    allowed = [str(item).strip() for item in profile.allowed_tools if str(item).strip()]
    if not allowed:
        return False
    return "*" in allowed or tool_name in allowed


def _mode_is_allowed(profile: ResolvedSkillProfile, requested_mode: str) -> bool:
    req_rank = _MODE_RANK.get(requested_mode, _MODE_RANK["implement"])
    max_rank = _MODE_RANK.get((profile.max_mode or "implement").strip().lower(), _MODE_RANK["implement"])
    return req_rank <= max_rank


def _policy_enforced(config: Config) -> bool:
    orchestrator = config.orchestrator
    mode = str(getattr(orchestrator, "mode", "legacy") or "legacy").strip().lower()
    return bool(orchestrator.enabled and mode == "hybrid" and not orchestrator.shadow_eval)
