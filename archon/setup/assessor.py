"""Capability assessment — what Archon can do alone vs. needs human help."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from archon.setup.models import SetupStep
from archon.setup.scanner import ProjectProfile


_SENSITIVE_ENV_PATTERNS = {
    "key", "token", "secret", "password", "credential",
    "auth", "api_key", "apikey", "private",
}


@dataclass
class HumanRequirement:
    what: str
    why: str
    how: str
    env_var: str = ""
    hint: str = ""


@dataclass
class AssessmentResult:
    already_done: list[str] = field(default_factory=list)
    archon_can: list[str] = field(default_factory=list)
    needs_human: list[HumanRequirement] = field(default_factory=list)

    def to_setup_steps(self) -> list[SetupStep]:
        steps: list[SetupStep] = []
        step_id = 1

        for desc in self.archon_can:
            steps.append(SetupStep(
                step_id=step_id, kind="archon", description=desc,
                status="pending", hint="", env_var="", provided=False,
            ))
            step_id += 1

        for req in self.needs_human:
            steps.append(SetupStep(
                step_id=step_id, kind="human", description=req.what,
                status="pending", hint=req.hint or req.how,
                env_var=req.env_var, provided=False,
            ))
            step_id += 1

        return steps


def assess_capabilities(profile: ProjectProfile) -> AssessmentResult:
    result = AssessmentResult()

    for var in profile.env_vars:
        if os.environ.get(var):
            result.already_done.append(f"{var} is already set")
        elif _is_sensitive(var):
            result.needs_human.append(HumanRequirement(
                what=f"Provide {var}",
                why=f"Required by {profile.project_name}",
                how=f"Set: export {var}=your_value",
                env_var=var,
                hint=_signup_hint(var),
            ))
        else:
            result.archon_can.append(f"Set {var} from project defaults or config")

    if profile.scripts:
        if "install" in profile.scripts or any("install" in v for v in profile.scripts.values()):
            result.archon_can.append("Run install command")
    elif "requirements.txt" in profile.discovery_sources:
        result.archon_can.append("Install Python dependencies (pip install -r requirements.txt)")
    elif "Cargo.toml" in profile.discovery_sources:
        result.archon_can.append("Build Rust project (cargo build)")

    return result


def _is_sensitive(var_name: str) -> bool:
    lower = var_name.lower()
    return any(pat in lower for pat in _SENSITIVE_ENV_PATTERNS)


def _signup_hint(var_name: str) -> str:
    lower = var_name.lower()
    if "openai" in lower:
        return "Sign up at https://platform.openai.com/api-keys"
    if "anthropic" in lower:
        return "Sign up at https://console.anthropic.com/"
    if "google" in lower or "gemini" in lower:
        return "Get key at https://aistudio.google.com/apikey"
    return ""
