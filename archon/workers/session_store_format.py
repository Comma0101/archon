"""Formatting helpers for worker session-store records."""

from archon.workers.session_store_models import WorkerApprovalRequest, WorkerSessionRecord


def format_worker_session_record(record: WorkerSessionRecord) -> str:
    effective_worker = record.selected_worker or record.requested_worker
    lines = [
        f"archon_session_id: {record.session_id}",
        f"status: {record.status}",
        f"requested_worker: {record.requested_worker}",
        f"selected_worker: {record.selected_worker}",
        f"effective_worker: {effective_worker}",
        f"mode: {record.mode}",
        f"repo_path: {record.repo_path}",
        f"created_at: {record.created_at}",
        f"completed_at: {record.completed_at}",
        f"event_count: {record.event_count}",
        f"turn_count: {record.turn_count}",
    ]
    if record.cancelled_at:
        lines.append(f"cancelled_at: {record.cancelled_at}")
    if record.vendor_session_id:
        lines.append(f"vendor_session_id: {record.vendor_session_id}")
    if record.exit_code is not None:
        lines.append(f"exit_code: {record.exit_code}")
    if record.summary:
        lines.append(f"summary: {record.summary}")
    if record.error:
        lines.append(f"error: {record.error}")
    lines.extend(["", "task:", record.task])
    if record.constraints.strip():
        lines.extend(["", "constraints:", record.constraints])
    return "\n".join(lines)


def format_worker_approvals(approvals: list[WorkerApprovalRequest]) -> str:
    if not approvals:
        return "No approval requests."
    lines = []
    for approval in approvals:
        line = (
            f"{approval.request_id}  {approval.status:<8} {approval.action}"
        )
        if approval.details:
            line += f"  {approval.details}"
        if approval.note:
            line += f"  note={approval.note}"
        lines.append(line)
    return "\n".join(lines)


def format_worker_session_list(records: list[WorkerSessionRecord]) -> str:
    if not records:
        return "No worker sessions recorded yet."
    lines = []
    for rec in records:
        line = (
            f"{rec.session_id}  {rec.status:<10} {rec.selected_worker or rec.requested_worker:<12} "
            f"{rec.mode:<9} {rec.updated_at}"
        )
        if rec.summary:
            line += f"  {rec.summary}"
        lines.append(line)
    return "\n".join(lines)

