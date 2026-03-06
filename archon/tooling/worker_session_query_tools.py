"""Worker session query/status tool registrations."""


def register_worker_session_query_tools(registry, ns) -> None:
    _truncate = lambda *a, **k: ns._truncate(*a, **k)
    format_worker_approvals = lambda *a, **k: ns.format_worker_approvals(*a, **k)
    format_worker_session_list = lambda *a, **k: ns.format_worker_session_list(*a, **k)
    format_worker_session_record = lambda *a, **k: ns.format_worker_session_record(*a, **k)
    get_background_run = lambda *a, **k: ns.get_background_run(*a, **k)
    list_worker_approvals = lambda *a, **k: ns.list_worker_approvals(*a, **k)
    list_worker_sessions = lambda *a, **k: ns.list_worker_sessions(*a, **k)
    load_worker_events = lambda *a, **k: ns.load_worker_events(*a, **k)
    load_worker_result = lambda *a, **k: ns.load_worker_result(*a, **k)
    load_worker_session = lambda *a, **k: ns.load_worker_session(*a, **k)
    _runtime_quiet_seconds = lambda *a, **k: ns._runtime_quiet_seconds(*a, **k)

    # worker_status
    def worker_status(session_id: str, include_events: bool = False, max_events: int = 25) -> str:
        record = load_worker_session(session_id)
        if record is None:
            return f"Worker session not found: {session_id}"

        lines = [format_worker_session_record(record)]
        active_run = get_background_run(session_id)
        if active_run:
            lines.extend([
                "",
                f"runtime_state: {active_run.state}",
                f"runtime_cancel_requested: {active_run.cancel_requested}",
                f"runtime_thread: {active_run.thread_name}",
            ])
            if active_run.pid:
                lines.append(f"runtime_pid: {active_run.pid}")
            if active_run.process_state:
                lines.append(f"runtime_process_state: {active_run.process_state}")
            if active_run.process_returncode is not None:
                lines.append(f"runtime_process_returncode: {active_run.process_returncode}")
            if active_run.cancel_signal_sent:
                lines.append(f"runtime_cancel_signal_sent: {active_run.cancel_signal_sent}")
            if active_run.last_output_at:
                lines.append(f"runtime_last_output_at: {active_run.last_output_at}")
            quiet_for = _runtime_quiet_seconds(active_run)
            if quiet_for is not None:
                lines.append(f"runtime_quiet_for_sec: {quiet_for}")
        pending_approvals = list_worker_approvals(session_id, pending_only=True)
        lines.append(f"pending_approvals: {len(pending_approvals)}")
        if pending_approvals:
            lines.extend(["", "pending_approval_requests:", format_worker_approvals(pending_approvals)])
        result = load_worker_result(session_id)
        if result and result.final_message.strip():
            lines.extend(["", "final_message:", _truncate(result.final_message.strip(), 4000)])
        if include_events:
            events = load_worker_events(session_id, limit=max_events)
            if events:
                lines.extend(["", "recent_events:"])
                for event in events:
                    preview = _truncate(str(event.payload), 300).replace("\n", " ")
                    lines.append(f"- {event.kind}: {preview}")
        return "\n".join(lines)

    registry.register(
        "worker_status",
        "Inspect a previously delegated worker run by Archon session ID.",
        {
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Archon worker session ID returned by delegate_code_task",
                },
                "include_events": {
                    "type": "boolean",
                    "description": "Include recent normalized worker events",
                    "default": False,
                },
                "max_events": {
                    "type": "integer",
                    "description": "Maximum recent events to include when include_events=true",
                    "default": 25,
                },
            },
            "required": ["session_id"],
        },
        worker_status,
    )

    # worker_list
    def worker_list(limit: int = 10) -> str:
        records = list_worker_sessions(limit=max(1, min(int(limit), 100)))
        return format_worker_session_list(records)

    registry.register(
        "worker_list",
        "List recent delegated worker sessions recorded by Archon.",
        {
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of recent sessions to list (1-100)",
                    "default": 10,
                }
            },
            "required": [],
        },
        worker_list,
    )

    # worker_poll
    def worker_poll(session_id: str, cursor: int = 0, max_events: int = 25) -> str:
        record = load_worker_session(session_id)
        if record is None:
            return f"Worker session not found: {session_id}"
        all_events = load_worker_events(session_id, limit=0)
        pending_approvals = list_worker_approvals(session_id, pending_only=True)
        start = max(0, int(cursor))
        end = min(len(all_events), start + max(1, int(max_events)))
        selected = all_events[start:end]
        active_run = get_background_run(session_id)
        lines = [
            f"archon_session_id: {session_id}",
            f"status: {record.status}",
            f"selected_worker: {record.selected_worker}",
            f"effective_worker: {record.selected_worker or record.requested_worker}",
            f"turn_count: {record.turn_count}",
            f"event_count: {record.event_count}",
            f"pending_approvals: {len(pending_approvals)}",
            f"cursor: {start}",
            f"next_cursor: {end}",
            f"returned_events: {len(selected)}",
        ]
        if active_run:
            lines.append(f"runtime_state: {active_run.state}")
            lines.append(f"runtime_cancel_requested: {active_run.cancel_requested}")
            if active_run.pid:
                lines.append(f"runtime_pid: {active_run.pid}")
            if active_run.process_state:
                lines.append(f"runtime_process_state: {active_run.process_state}")
            if active_run.process_returncode is not None:
                lines.append(f"runtime_process_returncode: {active_run.process_returncode}")
            if active_run.cancel_signal_sent:
                lines.append(f"runtime_cancel_signal_sent: {active_run.cancel_signal_sent}")
            if active_run.last_output_at:
                lines.append(f"runtime_last_output_at: {active_run.last_output_at}")
            quiet_for = _runtime_quiet_seconds(active_run)
            if quiet_for is not None:
                lines.append(f"runtime_quiet_for_sec: {quiet_for}")
        if record.summary:
            lines.append(f"summary: {record.summary}")
        if selected:
            lines.extend(["", "events:"])
            for event in selected:
                preview = _truncate(str(event.payload), 300).replace("\n", " ")
                lines.append(f"- {event.kind}: {preview}")
        if pending_approvals:
            lines.extend(["", "pending_approval_requests:", format_worker_approvals(pending_approvals)])
        return "\n".join(lines)

    registry.register(
        "worker_poll",
        "Poll a worker session for recent normalized events using a simple event cursor.",
        {
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Archon worker session ID",
                },
                "cursor": {
                    "type": "integer",
                    "description": "0-based event cursor offset to read from",
                    "default": 0,
                },
                "max_events": {
                    "type": "integer",
                    "description": "Maximum events to return from the cursor",
                    "default": 25,
                },
            },
            "required": ["session_id"],
        },
        worker_poll,
    )

