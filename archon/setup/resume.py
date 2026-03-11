"""Matching helpers for resuming blocked setup jobs from fresh user input."""

from __future__ import annotations

from dataclasses import dataclass, field

from archon.setup.models import SetupRecord

_GENERIC_SIGNALS = {
    "api",
    "key",
    "token",
    "secret",
    "password",
    "credential",
    "done",
    "ready",
    "completed",
}


@dataclass
class MatchResult:
    kind: str
    job: SetupRecord | None = None
    candidates: list[SetupRecord] = field(default_factory=list)
    matched_step_id: int | None = None


def match_input_to_blocked_job(user_message: str, blocked_records: list[SetupRecord]) -> MatchResult:
    if not blocked_records:
        return MatchResult(kind="no_blocked_jobs")

    msg = str(user_message or "").strip().lower()
    words = set(msg.replace("=", " ").replace(":", " ").split())
    scored: list[tuple[float, SetupRecord, int | None]] = []

    for record in blocked_records:
        score = 0.0
        matched_step_id = None
        project_name = str(record.project_name or "").strip().lower()
        if project_name and project_name in msg:
            score += 10.0

        for blocker in record.blocked_steps():
            step_score = 0.0
            env_var = str(blocker.env_var or "").strip().lower()
            if env_var and env_var in msg:
                step_score += 8.0
            env_words = set(env_var.replace("_", " ").split()) if env_var else set()
            overlap = env_words & words
            if overlap:
                step_score += float(len(overlap) * 2)
            description = str(
                getattr(blocker, "what", "") or getattr(blocker, "description", "") or ""
            ).strip().lower()
            desc_words = set(description.split())
            desc_overlap = desc_words & words
            if desc_overlap:
                step_score += float(len(desc_overlap))
            if step_score > 0 and (matched_step_id is None or step_score > score):
                matched_step_id = blocker.step_id
            score += step_score

        if score == 0 and (_GENERIC_SIGNALS & words):
            score += 1.0

        if score > 0:
            scored.append((score, record, matched_step_id))

    if not scored:
        return MatchResult(kind="no_match")

    scored.sort(key=lambda item: (-item[0], item[1].setup_id))
    if len(scored) == 1:
        return MatchResult(
            kind="single_match",
            job=scored[0][1],
            matched_step_id=scored[0][2],
        )

    top_score = scored[0][0]
    top = [item for item in scored if item[0] == top_score]
    if len(top) == 1:
        return MatchResult(
            kind="single_match",
            job=top[0][1],
            matched_step_id=top[0][2],
        )
    return MatchResult(kind="ambiguous", candidates=[item[1] for item in top[:5]])
