"""Formatting helpers for project setup jobs."""

from __future__ import annotations

from archon.control.jobs import job_summary_from_setup_record
from archon.setup.models import SetupRecord


def format_setup_record(record: SetupRecord) -> str:
    summary = job_summary_from_setup_record(record)
    blocked_steps = record.blocked_steps()
    pending_archon_steps = record.pending_archon_steps()
    done_steps = record.done_step_count()
    total_steps = len(record.steps)
    lines = [
        f"setup_id: {record.setup_id}",
        f"setup_status: {record.status}",
        f"job_id: setup:{record.setup_id}",
        "job_kind: project_setup",
        f"job_status: {record.status}",
        f"job_summary: {summary.summary}",
        f"job_last_update_at: {record.updated_at}",
        f"job_project: {record.project_name}",
        f"project_name: {record.project_name}",
        f"job_project_path: {record.project_path}",
        f"project_path: {record.project_path}",
        f"job_steps_completed: {done_steps}/{total_steps}",
        f"steps_completed: {done_steps}/{total_steps}",
    ]
    if record.stack:
        lines.append(f"job_stack: {record.stack}")
        lines.append(f"stack: {record.stack}")
    if record.approval_state:
        lines.append(f"approval_state: {record.approval_state}")
    if blocked_steps:
        lines.append("blocked_steps:")
        lines.append("job_blocked_on:")
        lines.append("blocked_on:")
        for step in blocked_steps:
            detail = f"- step {step.step_id}: {step.description}".rstrip()
            if step.env_var:
                detail = f"{detail} | env={step.env_var}"
            if step.hint:
                detail = f"{detail} | hint={step.hint}"
            lines.append(detail)
    if pending_archon_steps:
        lines.append("pending_archon_steps:")
        for step in pending_archon_steps:
            lines.append(f"- {step.description}")
        lines.append("job_pending_steps:")
        lines.append("pending_steps:")
        for step in pending_archon_steps:
            lines.append(f"- step {step.step_id} [{step.kind}] {step.description}")
    if record.discovery_sources:
        lines.append("discovery_sources: " + ", ".join(record.discovery_sources))
    if record.requirements:
        lines.append(f"requirements: {record.requirements}")
    if record.resume_hint:
        lines.append(f"job_resume_hint: {record.resume_hint}")
        lines.append(f"resume_hint: {record.resume_hint}")
    if record.generated_skill_path:
        lines.append(f"job_generated_skill_path: {record.generated_skill_path}")
        lines.append(f"generated_skill_path: {record.generated_skill_path}")
    if record.artifact_refs:
        lines.append("job_artifacts: " + ", ".join(record.artifact_refs))
        lines.append("artifacts: " + ", ".join(record.artifact_refs))
    return "\n".join(lines)
