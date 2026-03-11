"""Models for project setup jobs."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SetupStep:
    step_id: int
    kind: str
    description: str
    status: str
    hint: str = ""
    env_var: str = ""
    provided: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "id": int(self.step_id),
            "step_id": int(self.step_id),
            "kind": self.kind,
            "desc": self.description,
            "description": self.description,
            "status": self.status,
            "hint": self.hint,
            "env_var": self.env_var,
            "provided": bool(self.provided),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "SetupStep":
        return cls(
            step_id=int(data.get("step_id", data.get("id", 0)) or 0),
            kind=str(data.get("kind", "") or ""),
            description=str(data.get("description", data.get("desc", "")) or ""),
            status=str(data.get("status", "") or ""),
            hint=str(data.get("hint", "") or ""),
            env_var=str(data.get("env_var", "") or ""),
            provided=bool(data.get("provided", False)),
        )


def _normalize_steps(raw: object) -> list[SetupStep]:
    if not isinstance(raw, list):
        return []
    steps: list[SetupStep] = []
    for item in raw:
        if isinstance(item, SetupStep):
            steps.append(item)
        elif isinstance(item, dict):
            steps.append(SetupStep.from_dict(item))
    return steps


def _normalize_dict_list(raw: object) -> list[dict[str, object]]:
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _normalize_str_list(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if str(item or "").strip()]


def _normalize_requirements(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, object] = {}
    for key, value in raw.items():
        key_text = str(key or "").strip()
        if not key_text:
            continue
        if isinstance(value, list):
            normalized[key_text] = [str(item) for item in value if str(item or "").strip()]
        else:
            normalized[key_text] = value
    return normalized


@dataclass
class SetupRecord:
    setup_id: str
    project_name: str
    project_path: str
    status: str
    created_at: str
    updated_at: str
    stack: str = ""
    steps: list[SetupStep] = field(default_factory=list)
    blocked_on: list[dict[str, object]] = field(default_factory=list)
    requirements: dict[str, object] = field(default_factory=dict)
    discovery_sources: list[str] = field(default_factory=list)
    generated_skill_path: str = ""
    resume_hint: str = ""
    approval_state: str = ""
    artifact_refs: list[str] = field(default_factory=list)
    summary: str = ""

    def __post_init__(self) -> None:
        self.steps = _normalize_steps(self.steps)
        self.blocked_on = _normalize_dict_list(self.blocked_on)
        self.requirements = _normalize_requirements(self.requirements)
        self.discovery_sources = _normalize_str_list(self.discovery_sources)
        self.artifact_refs = _normalize_str_list(self.artifact_refs)

    def to_dict(self) -> dict[str, object]:
        return {
            "setup_id": self.setup_id,
            "project_name": self.project_name,
            "project_path": self.project_path,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "stack": self.stack,
            "steps": [step.to_dict() for step in self.steps],
            "blocked_on": [dict(item) for item in self.blocked_on],
            "requirements": dict(self.requirements),
            "discovery_sources": list(self.discovery_sources),
            "generated_skill_path": self.generated_skill_path,
            "resume_hint": self.resume_hint,
            "approval_state": self.approval_state,
            "artifact_refs": list(self.artifact_refs),
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "SetupRecord":
        return cls(
            setup_id=str(data.get("setup_id", "") or ""),
            project_name=str(data.get("project_name", "") or ""),
            project_path=str(data.get("project_path", "") or ""),
            status=str(data.get("status", "") or ""),
            created_at=str(data.get("created_at", "") or ""),
            updated_at=str(data.get("updated_at", "") or ""),
            stack=str(data.get("stack", "") or ""),
            steps=_normalize_steps(data.get("steps")),
            blocked_on=_normalize_dict_list(data.get("blocked_on")),
            requirements=_normalize_requirements(data.get("requirements")),
            discovery_sources=_normalize_str_list(data.get("discovery_sources")),
            generated_skill_path=str(data.get("generated_skill_path", "") or ""),
            resume_hint=str(data.get("resume_hint", "") or ""),
            approval_state=str(data.get("approval_state", "") or ""),
            artifact_refs=_normalize_str_list(data.get("artifact_refs")),
            summary=str(data.get("summary", "") or ""),
        )

    def blocked_steps(self) -> list[SetupStep]:
        blocked: list[SetupStep] = []
        seen: set[tuple[int, str, str]] = set()
        for step in self.steps:
            if str(step.kind or "").strip().lower() != "human":
                continue
            if str(step.status or "").strip().lower() in {"done", "completed"}:
                continue
            key = (int(step.step_id or 0), step.env_var, step.description)
            if key not in seen:
                seen.add(key)
                blocked.append(step)
        for item in self.blocked_on:
            step = SetupStep.from_dict(item)
            if not step.kind:
                step.kind = "human"
            if not step.description:
                step.description = str(item.get("what", "") or "")
            key = (int(step.step_id or 0), step.env_var, step.description)
            if key not in seen:
                seen.add(key)
                blocked.append(step)
        return blocked

    def pending_archon_steps(self) -> list[SetupStep]:
        pending: list[SetupStep] = []
        for step in self.steps:
            if str(step.kind or "").strip().lower() == "human":
                continue
            if str(step.status or "").strip().lower() in {"done", "completed"}:
                continue
            pending.append(step)
        return pending

    def done_step_count(self) -> int:
        return sum(
            1
            for step in self.steps
            if str(step.status or "").strip().lower() in {"done", "completed"}
        )

    def completed_step_count(self) -> int:
        return self.done_step_count()
