"""Worker session action/control tool registrations."""

from pathlib import Path

from archon.safety import Level


def register_worker_session_action_tools(registry, ns):
    format_worker_approvals = lambda *a, **k: ns.format_worker_approvals(*a, **k)
    format_worker_result = lambda *a, **k: ns.format_worker_result(*a, **k)
    format_worker_session_record = lambda *a, **k: ns.format_worker_session_record(*a, **k)
    get_background_run = lambda *a, **k: ns.get_background_run(*a, **k)
    list_worker_approvals = lambda *a, **k: ns.list_worker_approvals(*a, **k)
    load_worker_session = lambda *a, **k: ns.load_worker_session(*a, **k)
    load_worker_task = lambda *a, **k: ns.load_worker_task(*a, **k)
    append_worker_turn = lambda *a, **k: ns.append_worker_turn(*a, **k)
    cancel_worker_session = lambda *a, **k: ns.cancel_worker_session(*a, **k)
    decide_worker_approval = lambda *a, **k: ns.decide_worker_approval(*a, **k)
    reconcile_worker_session = lambda *a, **k: ns.reconcile_worker_session(*a, **k)
    request_background_cancel = lambda *a, **k: ns.request_background_cancel(*a, **k)
    run_worker_task = lambda *a, **k: ns.run_worker_task(*a, **k)
    start_background_worker = lambda *a, **k: ns.start_background_worker(*a, **k)
    WorkerTask = ns.WorkerTask
    _runtime_quiet_seconds = lambda *a, **k: ns._runtime_quiet_seconds(*a, **k)

    # worker_start
    def worker_start(
        task: str,
        worker: str = "claude_code",
        mode: str = "review",
        repo_path: str = ".",
        timeout_sec: int = 900,
        constraints: str = "",
        background: bool = True,
    ) -> str:
        mode_value = (mode or "review").strip().lower()
        repo = Path(repo_path).expanduser().resolve()
        if not repo.exists():
            return f"Error: Repository path not found: {repo}"
        if not repo.is_dir():
            return f"Error: Repository path is not a directory: {repo}"
        return registry._run_and_record_delegated_task(
            task=task,
            worker=worker,
            mode_value=mode_value,
            repo=repo,
            timeout_sec=int(timeout_sec),
            constraints=constraints,
            confirm_prefix="Start worker session",
            background=bool(background),
        )

    registry.register(
        "worker_start",
        "Start a delegated worker session and record it with an Archon session ID. Use this when you plan follow-up messages with worker_send.",
        {
            "properties": {
                "task": {"type": "string", "description": "Initial delegated task / prompt"},
                "worker": {
                    "type": "string",
                    "description": "Preferred worker: auto | codex | claude_code | opencode",
                    "default": "claude_code",
                },
                "mode": {
                    "type": "string",
                    "description": "Task mode: analyze | review | implement | debug",
                    "default": "review",
                },
                "repo_path": {
                    "type": "string",
                    "description": "Repository directory for the worker session",
                    "default": ".",
                },
                "timeout_sec": {
                    "type": "integer",
                    "description": "Timeout for each worker turn in seconds",
                    "default": 900,
                },
                "constraints": {
                    "type": "string",
                    "description": "Optional constraints to keep applying on follow-up turns",
                    "default": "",
                },
                "background": {
                    "type": "boolean",
                    "description": "Run in background (recommended for interactive/approval-heavy sessions)",
                    "default": True,
                },
            },
            "required": ["task"],
        },
        worker_start,
    )

    # worker_send
    def worker_send(
        session_id: str,
        message: str,
        timeout_sec: int | None = None,
        mode: str | None = None,
        background: bool = False,
    ) -> str:
        record = load_worker_session(session_id)
        if record is None:
            return f"Worker session not found: {session_id}"
        if record.status == "cancelled":
            return f"Worker session is cancelled: {session_id}"
        pending_approvals = list_worker_approvals(session_id, pending_only=True)
        if pending_approvals:
            return (
                f"Worker session {session_id} has {len(pending_approvals)} pending approval request(s). "
                "Use worker_status/worker_poll to inspect and worker_approve to decide before continuing."
            )
        if not record.vendor_session_id:
            return (
                "This worker session cannot be continued because no vendor session ID was captured. "
                "Try starting a new session with `worker_start`."
            )
        if record.selected_worker not in {"claude_code", "opencode"}:
            return (
                f"worker_send is not implemented yet for worker '{record.selected_worker}'. "
                "Supported for Claude Code and OpenCode sessions."
            )

        mode_value = (mode if mode is not None else record.mode or "review").strip().lower()
        level = Level.SAFE if mode_value in {"review", "analyze"} else Level.DANGEROUS
        msg_preview = " ".join(message.split())
        if len(msg_preview) > 140:
            msg_preview = msg_preview[:140] + "..."
        if not registry.confirmer(
            f"Continue worker session {session_id} ({record.selected_worker}/{mode_value}): {msg_preview}",
            level,
        ):
            return "Delegation rejected by safety gate."

        base_task = load_worker_task(session_id)
        timeout_value = int(timeout_sec) if timeout_sec is not None else int(
            (base_task.timeout_sec if base_task else record.timeout_sec) or 900
        )
        constraints_value = base_task.constraints if base_task else record.constraints
        repo = Path(record.repo_path).expanduser().resolve()
        if not repo.exists() or not repo.is_dir():
            return f"Error: Repository path unavailable for session: {repo}"

        task_obj = WorkerTask(
            task=message,
            worker=record.selected_worker,
            mode=mode_value,
            repo_path=str(repo),
            timeout_sec=timeout_value,
            constraints=constraints_value,
            model=(base_task.model if base_task else ""),
            resume_vendor_session_id=record.vendor_session_id,
            archon_session_id=session_id,
        )
        if background:
            try:
                active = start_background_worker(task_obj, requested_worker=record.selected_worker)
            except Exception as e:
                return f"Error: failed to start background worker follow-up ({type(e).__name__}: {e})"
            registry._set_worker_session_affinity(
                active.session_id, str(repo), record.selected_worker, record.requested_worker
            )
            return (
                f"archon_session_id: {active.session_id}\n"
                f"job_id: worker:{active.session_id}\n"
                f"selected_worker: {record.selected_worker}\n"
                f"mode: {mode_value}\n"
                f"background: started\n"
                f"runtime_state: {active.state}\n"
                "Use worker_poll / worker_status to track progress and worker_approve if approvals are requested."
            )

        result = run_worker_task(task_obj)
        updated = append_worker_turn(session_id, task_obj, result)
        if updated is None:
            return (
                f"archon_session_id: {session_id}\n"
                f"job_id: worker:{session_id}\n"
                "worker_session_recording: error (failed to append follow-up turn)"
            )
        registry._set_worker_session_affinity(
            session_id,
            str(repo),
            record.selected_worker,
            record.requested_worker,
            updated.selected_worker,
        )
        return (
            f"archon_session_id: {session_id}\n"
            f"job_id: worker:{session_id}\n"
            f"selected_worker: {record.selected_worker}\n"
            f"recorded_at: {updated.updated_at}\n"
            f"turn_count: {updated.turn_count}\n\n"
            + format_worker_result(result)
        )

    registry.register(
        "worker_send",
        "Send a follow-up message to a previously started worker session (Claude Code review/analyze sessions supported first).",
        {
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Archon worker session ID returned by worker_start/delegate_code_task",
                },
                "message": {
                    "type": "string",
                    "description": "Follow-up instruction for the delegated worker session",
                },
                "timeout_sec": {
                    "type": "integer",
                    "description": "Optional per-turn timeout override in seconds",
                },
                "mode": {
                    "type": "string",
                    "description": "Optional mode override for this turn (review | analyze | implement | debug). Useful when continuing a session but escalating task type.",
                },
                "background": {
                    "type": "boolean",
                    "description": "Run this follow-up turn in background (recommended if approvals may be needed)",
                    "default": False,
                },
            },
            "required": ["session_id", "message"],
        },
        worker_send,
    )

    # worker_cancel
    def worker_cancel(session_id: str, reason: str = "Cancelled by user") -> str:
        record = load_worker_session(session_id)
        if record is None:
            return f"Worker session not found: {session_id}"
        if record.status == "cancelled":
            return format_worker_session_record(record)
        runtime_cancelled = request_background_cancel(session_id)
        updated = cancel_worker_session(session_id, reason=reason)
        if updated is None:
            return f"Worker session not found: {session_id}"
        registry._clear_worker_session_affinity(session_id)
        result = format_worker_session_record(updated)
        if runtime_cancelled:
            result += "\n\nruntime_cancel: requested (runtime sent terminate/kill best-effort to the active worker subprocess)"
        return result

    registry.register(
        "worker_cancel",
        "Cancel/close a worker session in Archon so no further worker_send turns are allowed; for background runs Archon sends terminate/kill best-effort to the active worker subprocess.",
        {
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Archon worker session ID",
                },
                "reason": {
                    "type": "string",
                    "description": "Optional cancellation reason recorded in the session log",
                    "default": "Cancelled by user",
                },
            },
            "required": ["session_id"],
        },
        worker_cancel,
    )

    # worker_approve
    def worker_approve(session_id: str, request_id: str, decision: str, note: str = "") -> str:
        record = load_worker_session(session_id)
        if record is None:
            return f"Worker session not found: {session_id}"
        decision_value = decision.strip().lower()
        if decision_value not in {"approve", "deny", "approved", "denied"}:
            return "Error: decision must be 'approve' or 'deny'"
        if not registry.confirmer(
            f"Worker approval decision for {session_id} request {request_id}: {decision_value}",
            Level.SAFE,
        ):
            return "Approval decision rejected by safety gate."
        updated = decide_worker_approval(session_id, request_id, decision_value, note=note)
        if updated is None:
            return (
                f"Approval request not found or invalid decision.\n"
                f"session_id: {session_id}\nrequest_id: {request_id}"
            )
        lines = [
            f"archon_session_id: {session_id}",
            f"request_id: {updated.request_id}",
            f"status: {updated.status}",
            f"action: {updated.action}",
        ]
        if updated.details:
            lines.append(f"details: {updated.details}")
        if updated.note:
            lines.append(f"note: {updated.note}")
        return "\n".join(lines)

    registry.register(
        "worker_approve",
        "Approve or deny a pending worker permission request recorded in an Archon worker session (approval broker data plane).",
        {
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Archon worker session ID",
                },
                "request_id": {
                    "type": "string",
                    "description": "Pending approval request ID (see worker_status/worker_poll)",
                },
                "decision": {
                    "type": "string",
                    "description": "approve or deny",
                },
                "note": {
                    "type": "string",
                    "description": "Optional note attached to the approval decision",
                    "default": "",
                },
            },
            "required": ["session_id", "request_id", "decision"],
        },
        worker_approve,
    )

    # worker_reconcile
    def worker_reconcile(
        session_id: str,
        reason: str = "Reconciled orphaned worker session",
        terminal_status: str = "error",
        force: bool = False,
    ) -> str:
        record = load_worker_session(session_id)
        if record is None:
            return f"Worker session not found: {session_id}"

        active_run = get_background_run(session_id)
        if active_run and active_run.state in {"starting", "running"} and not force:
            lines = [
                f"archon_session_id: {session_id}",
                "reconcile: refused (active background run still present)",
                f"runtime_state: {active_run.state}",
            ]
            if active_run.pid:
                lines.append(f"runtime_pid: {active_run.pid}")
            if active_run.process_state:
                lines.append(f"runtime_process_state: {active_run.process_state}")
            if active_run.process_returncode is not None:
                lines.append(f"runtime_process_returncode: {active_run.process_returncode}")
            quiet_for = _runtime_quiet_seconds(active_run)
            if quiet_for is not None:
                lines.append(f"runtime_quiet_for_sec: {quiet_for}")
            lines.append("Use worker_poll/worker_status, worker_cancel, or retry with force=true if the runtime is stale.")
            return "\n".join(lines)

        if not registry.confirmer(
            f"Reconcile worker session {session_id} as {terminal_status}: {reason}",
            Level.SAFE,
        ):
            return "Worker reconcile rejected by safety gate."

        updated = reconcile_worker_session(
            session_id,
            reason=reason,
            terminal_status=terminal_status,
        )
        if updated is None:
            return f"Worker session not found: {session_id}"

        lines = [format_worker_session_record(updated)]
        if active_run and active_run.state in {"starting", "running"} and force:
            lines.extend([
                "",
                "warning: reconciled while an active runtime entry still exists (force=true).",
                "Use worker_cancel if the subprocess is still alive.",
            ])
        return "\n".join(lines)

    registry.register(
        "worker_reconcile",
        "Repair a stuck/orphaned worker session record (for example, session says running but no live runtime remains). Refuses active runs unless force=true.",
        {
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Archon worker session ID",
                },
                "reason": {
                    "type": "string",
                    "description": "Reason recorded in the reconciliation event/log",
                    "default": "Reconciled orphaned worker session",
                },
                "terminal_status": {
                    "type": "string",
                    "description": "Terminal status to force: error | failed | cancelled | paused",
                    "default": "error",
                },
                "force": {
                    "type": "boolean",
                    "description": "Allow reconcile even if an active runtime entry is still present",
                    "default": False,
                },
            },
            "required": ["session_id"],
        },
        worker_reconcile,
    )

    return {"worker_send": worker_send}
