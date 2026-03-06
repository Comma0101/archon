"""Worker session-store data models."""

from dataclasses import dataclass


@dataclass
class WorkerSessionRecord:
    session_id: str
    created_at: str
    updated_at: str
    completed_at: str
    requested_worker: str
    selected_worker: str
    mode: str
    status: str
    repo_path: str
    task: str
    constraints: str
    timeout_sec: int
    summary: str
    exit_code: int | None
    error: str
    vendor_session_id: str = ""
    event_count: int = 0
    turn_count: int = 1
    cancelled_at: str = ""

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "requested_worker": self.requested_worker,
            "selected_worker": self.selected_worker,
            "mode": self.mode,
            "status": self.status,
            "repo_path": self.repo_path,
            "task": self.task,
            "constraints": self.constraints,
            "timeout_sec": self.timeout_sec,
            "summary": self.summary,
            "exit_code": self.exit_code,
            "error": self.error,
            "vendor_session_id": self.vendor_session_id,
            "event_count": self.event_count,
            "turn_count": self.turn_count,
            "cancelled_at": self.cancelled_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WorkerSessionRecord":
        return cls(
            session_id=str(data.get("session_id", "")),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            completed_at=str(data.get("completed_at", "")),
            requested_worker=str(data.get("requested_worker", "")),
            selected_worker=str(data.get("selected_worker", "")),
            mode=str(data.get("mode", "")),
            status=str(data.get("status", "")),
            repo_path=str(data.get("repo_path", "")),
            task=str(data.get("task", "")),
            constraints=str(data.get("constraints", "")),
            timeout_sec=int(data.get("timeout_sec", 0)),
            summary=str(data.get("summary", "")),
            exit_code=data.get("exit_code"),
            error=str(data.get("error", "")),
            vendor_session_id=str(data.get("vendor_session_id", "")),
            event_count=int(data.get("event_count", 0)),
            turn_count=int(data.get("turn_count", 1)),
            cancelled_at=str(data.get("cancelled_at", "")),
        )


@dataclass
class WorkerApprovalRequest:
    request_id: str
    status: str  # pending | approved | denied
    action: str
    details: str
    created_at: str
    decided_at: str = ""
    decision: str = ""
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "status": self.status,
            "action": self.action,
            "details": self.details,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
            "decision": self.decision,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WorkerApprovalRequest":
        return cls(
            request_id=str(data.get("request_id", "")),
            status=str(data.get("status", "pending")),
            action=str(data.get("action", "")),
            details=str(data.get("details", "")),
            created_at=str(data.get("created_at", "")),
            decided_at=str(data.get("decided_at", "")),
            decision=str(data.get("decision", "")),
            note=str(data.get("note", "")),
        )

