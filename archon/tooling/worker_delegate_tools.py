"""Worker delegate tool registration extracted from worker_tools.py."""

from pathlib import Path

from archon.safety import Level


def register_delegate_tool(registry, ns, worker_send):
    """Register delegate_code_task and attach shared delegate runner to registry."""
    WorkerTask = ns.WorkerTask
    start_background_worker = lambda *a, **k: ns.start_background_worker(*a, **k)
    reserve_worker_session = lambda *a, **k: ns.reserve_worker_session(*a, **k)
    run_worker_task = lambda *a, **k: ns.run_worker_task(*a, **k)
    record_worker_run = lambda *a, **k: ns.record_worker_run(*a, **k)
    format_worker_result = lambda *a, **k: ns.format_worker_result(*a, **k)
    get_background_run = lambda *a, **k: ns.get_background_run(*a, **k)
    load_worker_session = lambda *a, **k: ns.load_worker_session(*a, **k)
    _runtime_quiet_seconds = lambda *a, **k: ns._runtime_quiet_seconds(*a, **k)
    _choose_delegate_execution_mode = lambda *a, **k: ns._choose_delegate_execution_mode(*a, **k)
    _detect_delegate_continue_target_worker = lambda *a, **k: ns._detect_delegate_continue_target_worker(*a, **k)
    _detect_delegate_force_new_session = lambda *a, **k: ns._detect_delegate_force_new_session(*a, **k)
    _worker_supporting_resume_key = lambda *a, **k: ns._worker_supporting_resume_key(*a, **k)
    _find_latest_worker_session_for_repo = lambda *a, **k: ns._find_latest_worker_session_for_repo(*a, **k)

    def _run_and_record_delegated_task(
        *,
        task: str,
        worker: str,
        mode_value: str,
        repo: Path,
        timeout_sec: int,
        constraints: str,
        confirm_prefix: str,
        background: bool = False,
        existing_session_id: str = "",
        model: str = "",
    ) -> str:
        level = Level.SAFE if mode_value in {"review", "analyze"} else Level.DANGEROUS
        task_preview = " ".join(task.split())
        if len(task_preview) > 140:
            task_preview = task_preview[:140] + "..."
        confirm_label = f"{confirm_prefix} {worker}/{mode_value} in {repo}: {task_preview}"
        if not registry.confirmer(confirm_label, level):
            return "Delegation rejected by safety gate."

        task_obj = WorkerTask(
            task=task,
            worker=worker,
            mode=mode_value,
            repo_path=str(repo),
            timeout_sec=int(timeout_sec),
            constraints=constraints,
            model=model,
            archon_session_id=existing_session_id,
        )
        if background:
            try:
                active = start_background_worker(task_obj, requested_worker=worker)
            except Exception as e:
                return f"Error: failed to start background worker ({type(e).__name__}: {e})"
            registry._set_worker_session_affinity(active.session_id, str(repo), worker)
            return (
                f"archon_session_id: {active.session_id}\n"
                f"background: started\n"
                f"runtime_state: {active.state}\n"
                f"requested_worker: {active.requested_worker}\n"
                f"mode: {mode_value}\n"
                f"repo_path: {repo}\n"
                "Use worker_poll / worker_status to track progress and worker_approve if approvals are requested. "
                "Avoid repeated worker_poll calls in the same turn for long jobs."
            )

        try:
            reserved = reserve_worker_session(task_obj, requested_worker=worker)
            task_obj.archon_session_id = reserved.session_id
        except Exception as e:
            return f"Error: failed to reserve worker session ({type(e).__name__}: {e})"
        result = run_worker_task(task_obj)
        try:
            session_record = record_worker_run(task_obj, result, requested_worker=worker)
        except Exception as e:
            return (
                format_worker_result(result)
                + f"\n\nworker_session_recording: error ({type(e).__name__}: {e})"
            )
        registry._set_worker_session_affinity(
            session_record.session_id,
            str(repo),
            worker,
            session_record.selected_worker or result.worker,
        )

        return (
            f"archon_session_id: {session_record.session_id}\n"
            f"requested_worker: {worker}\n"
            f"selected_worker: {session_record.selected_worker or result.worker}\n"
            f"recorded_at: {session_record.completed_at}\n\n"
            + format_worker_result(result)
        )

    def _try_delegate_session_reuse(
        *,
        session_record,
        continue_message: str,
        mode_value: str,
        timeout_sec: int,
        planned_mode: str,
        execution_mode: str,
        planned_reason: str,
        reuse_reason: str,
        requested_worker: str,
        repo: str,
    ) -> str | None:
        rec_worker = ((session_record.selected_worker or session_record.requested_worker) or "").strip().lower()
        if rec_worker not in {"claude_code", "opencode"}:
            return None

        active_run = get_background_run(session_record.session_id)
        if active_run and active_run.state in {"starting", "running"}:
            lines = [
                "session_reuse: blocked_active_session",
                f"reuse_reason: {reuse_reason}",
                f"matched_archon_session_id: {session_record.session_id}",
                f"matched_worker: {rec_worker}",
                f"matched_status: {session_record.status}",
                f"runtime_state: {active_run.state}",
            ]
            if active_run.pid:
                lines.append(f"runtime_pid: {active_run.pid}")
            quiet_for = _runtime_quiet_seconds(active_run)
            if quiet_for is not None:
                lines.append(f"runtime_quiet_for_sec: {quiet_for}")
            lines.append("The matched worker session is still running. Use worker_poll / worker_status instead of starting a new delegation.")
            return "\n".join(lines)

        if session_record.status == "running" and not active_run:
            return (
                "session_reuse: blocked_stale_running_record\n"
                f"reuse_reason: {reuse_reason}\n"
                f"matched_archon_session_id: {session_record.session_id}\n"
                f"matched_worker: {rec_worker}\n"
                "The matched session is marked running but has no active runtime entry. "
                "Check worker_status/worker_poll and use worker_reconcile if it is stale."
            )

        rerouted = worker_send(
            session_id=session_record.session_id,
            message=continue_message,
            timeout_sec=int(timeout_sec),
            mode=mode_value,
            background=(planned_mode == "background"),
        )
        header = [
            "session_reuse: auto_continue_latest" if reuse_reason == "continuation_phrase" else "session_reuse: reused_sticky_session",
            f"reuse_reason: {reuse_reason}",
            f"matched_archon_session_id: {session_record.session_id}",
            f"matched_worker: {rec_worker}",
            f"execution_mode: {planned_mode}",
        ]
        if requested_worker and requested_worker.strip():
            header.append(f"requested_worker: {requested_worker.strip().lower()}")
        if execution_mode and execution_mode.strip():
            header.append(f"requested_execution_mode: {execution_mode.strip().lower()}")
        if planned_reason:
            header.append(f"execution_reason: {planned_reason}")
        return "\n".join(header) + "\n" + rerouted

    registry._run_and_record_delegated_task = _run_and_record_delegated_task

    def delegate_code_task(
        task: str,
        worker: str = "auto",
        mode: str = "implement",
        repo_path: str = ".",
        timeout_sec: int = 900,
        constraints: str = "",
        execution_mode: str = "auto",
    ) -> str:
        mode_value = (mode or "implement").strip().lower()
        repo = Path(repo_path).expanduser().resolve()
        if not repo.exists():
            return f"Error: Repository path not found: {repo}"
        if not repo.is_dir():
            return f"Error: Repository path is not a directory: {repo}"
        planned_mode, planned_reason = _choose_delegate_execution_mode(
            task=task,
            mode=mode_value,
            timeout_sec=int(timeout_sec),
            requested_execution_mode=execution_mode,
        )
        if planned_mode == "invalid":
            return f"Error: {planned_reason}"
        explicit_new_session = _detect_delegate_force_new_session(task)
        if planned_mode != "oneshot" and not explicit_new_session:
            sticky_target_worker = _worker_supporting_resume_key(worker)
            if sticky_target_worker:
                sticky_session_id = registry._get_worker_session_affinity(sticky_target_worker, str(repo))
                if sticky_session_id:
                    sticky_record = load_worker_session(sticky_session_id)
                    if sticky_record is not None:
                        rerouted = _try_delegate_session_reuse(
                            session_record=sticky_record,
                            continue_message=task,
                            mode_value=mode_value,
                            timeout_sec=int(timeout_sec),
                            planned_mode=planned_mode,
                            execution_mode=execution_mode,
                            planned_reason=planned_reason,
                            reuse_reason="sticky_session_default",
                            requested_worker=worker,
                            repo=str(repo),
                        )
                        if rerouted is not None:
                            return rerouted

        continue_target_worker = _detect_delegate_continue_target_worker(
            task=task,
            requested_worker=worker,
            requested_execution_mode=execution_mode,
        )
        if continue_target_worker and not explicit_new_session:
            latest = _find_latest_worker_session_for_repo(
                worker=continue_target_worker,
                repo_path=str(repo),
            )
            if latest is not None:
                rerouted = _try_delegate_session_reuse(
                    session_record=latest,
                    continue_message=task,
                    mode_value=mode_value,
                    timeout_sec=int(timeout_sec),
                    planned_mode=planned_mode,
                    execution_mode=execution_mode,
                    planned_reason=planned_reason,
                    reuse_reason="continuation_phrase",
                    requested_worker=worker,
                    repo=str(repo),
                )
                if rerouted is not None:
                    return rerouted
        result = _run_and_record_delegated_task(
            task=task,
            worker=worker,
            mode_value=mode_value,
            repo=repo,
            timeout_sec=int(timeout_sec),
            constraints=constraints,
            confirm_prefix="Delegate",
            background=(planned_mode == "background"),
        )
        header = [
            f"execution_mode: {planned_mode}",
        ]
        if execution_mode and execution_mode.strip():
            header.append(f"requested_execution_mode: {execution_mode.strip().lower()}")
        if planned_reason:
            header.append(f"execution_reason: {planned_reason}")
        if explicit_new_session:
            header.append("session_policy: explicit_new_session")
        return "\n".join(header) + "\n" + result

    registry.register(
        "delegate_code_task",
        "Delegate a coding task to an external coding worker (auto routes to Codex/Claude Code/OpenCode when available). `execution_mode=auto` defaults to background sessions for deep/long tasks.",
        {
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Exact coding task to delegate",
                },
                "worker": {
                    "type": "string",
                    "description": "Preferred worker: auto | codex | claude_code | opencode",
                    "default": "auto",
                },
                "mode": {
                    "type": "string",
                    "description": "Task mode: analyze | review | implement | debug (implement/debug are treated as dangerous)",
                    "default": "implement",
                },
                "repo_path": {
                    "type": "string",
                    "description": "Repository directory to run the worker in",
                    "default": ".",
                },
                "timeout_sec": {
                    "type": "integer",
                    "description": "Timeout in seconds for the delegated run",
                    "default": 900,
                },
                "constraints": {
                    "type": "string",
                    "description": "Optional additional constraints appended to the delegated prompt",
                    "default": "",
                },
                "execution_mode": {
                    "type": "string",
                    "description": "How to run the delegation: auto | oneshot | background. `auto` prefers background for deep/long tasks.",
                    "default": "auto",
                },
            },
            "required": ["task"],
        },
        delegate_code_task,
    )
