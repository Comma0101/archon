"""Interactive/runtime command helpers for Archon CLI."""

from __future__ import annotations

from archon.cli_ui import _format_terminal_approval_required
from archon.control.contracts import HookEvent
from archon.safety import Level


PENDING_APPROVAL_TTL_SEC = 5 * 60
APPROVAL_COMMAND_PREVIEW_LIMIT = 240


def _truncate_terminal_approval_command(command: str, limit: int = APPROVAL_COMMAND_PREVIEW_LIMIT) -> str:
    command = (command or "").strip()
    if len(command) <= limit:
        return command
    return command[: max(0, limit - 3)] + "..."


def _looks_like_safety_gate_rejection(response: str | None) -> bool:
    if not isinstance(response, str):
        return False
    lowered = response.lower()
    return (
        "rejected by safety gate" in lowered
        or "self-modification rejected" in lowered
        or lowered.startswith("forbidden:")
    )


def chat_cmd(
    *,
    make_agent_fn,
    make_telegram_adapter_fn,
    new_session_id_fn,
    save_exchange_fn,
    slash_completer_fn,
    pick_slash_command_fn,
    is_bracketed_paste_start_fn,
    collect_bracketed_paste_fn,
    is_paste_command_fn,
    collect_paste_message_fn,
    handle_repl_command_fn,
    is_model_runtime_error_fn,
    format_session_summary_fn,
    format_chat_response_fn,
    format_turn_stats_fn,
    make_readline_prompt_fn,
    spinner_cls,
    ansi_prompt_user: str,
    ansi_error: str,
    ansi_reset: str,
    click_echo_fn,
    input_fn,
    readline_module,
    time_time_fn,
    version: str,
) -> None:
    """Interactive chat REPL body."""
    agent = make_agent_fn()
    telegram_adapter = None
    session_id = new_session_id_fn()

    tg = agent.config.telegram
    if tg.enabled and tg.connect_on_chat:
        try:
            telegram_adapter = make_telegram_adapter_fn(agent.config)
            telegram_adapter.start()
            click_echo_fn(
                f"[telegram] connected (users={len(tg.allowed_user_ids)}, poll={tg.poll_timeout_sec}s)"
            )
        except Exception as e:
            click_echo_fn(f"[telegram] disabled: {e}", err=True)

    spinner = spinner_cls()
    route_state = {"lane": "", "reason": ""}
    phase_state = {"label": ""}
    route_counts: dict[str, int] = {}
    counted_route_turn_ids: set[str] = set()
    approval_state = {
        "dangerous_mode": False,
        "approve_next_tokens": 0,
        "pending_request": None,
        "next_approval_id": 1,
        "current_user_input": "",
        "blocked_pending_id": "",
    }

    def pending_is_expired(pending: dict | None) -> bool:
        if not isinstance(pending, dict):
            return False
        expires_at = pending.get("expires_at")
        return isinstance(expires_at, (int, float)) and expires_at <= time_time_fn()

    def clear_expired_pending() -> dict | None:
        pending = approval_state.get("pending_request")
        if pending_is_expired(pending):
            if isinstance(pending, dict):
                pending["status"] = "expired"
            approval_state["pending_request"] = None
            return None
        return pending if isinstance(pending, dict) else None

    def queue_pending_approval(command: str) -> dict:
        now = time_time_fn()
        approval_id = f"{session_id}:{approval_state['next_approval_id']}"
        approval_state["next_approval_id"] += 1
        pending = {
            "approval_id": approval_id,
            "status": "pending",
            "created_at": now,
            "expires_at": now + PENDING_APPROVAL_TTL_SEC,
            "blocked_command_preview": _truncate_terminal_approval_command(command),
            "blocked_user_input": str(approval_state.get("current_user_input") or ""),
        }
        approval_state["pending_request"] = pending
        return pending

    def get_terminal_approval_status() -> dict:
        pending = clear_expired_pending()
        preview = ""
        if pending is not None:
            preview = str(pending.get("blocked_command_preview") or "").strip()
        return {
            "dangerous_mode": bool(approval_state.get("dangerous_mode", False)),
            "approve_next_tokens": max(0, int(approval_state.get("approve_next_tokens", 0) or 0)),
            "pending": preview or "none",
            "pending_command_preview": preview,
            "pending_request": dict(pending) if pending is not None else None,
        }

    def confirm_for_terminal_session(command: str, level: Level) -> bool:
        approval_state["blocked_pending_id"] = ""
        if level == Level.SAFE:
            return True
        if level == Level.FORBIDDEN:
            return False
        if bool(approval_state.get("dangerous_mode", False)):
            return True
        tokens = max(0, int(approval_state.get("approve_next_tokens", 0) or 0))
        if tokens > 0:
            approval_state["approve_next_tokens"] = tokens - 1
            return True
        pending = queue_pending_approval(command)
        approval_state["blocked_pending_id"] = str(pending.get("approval_id") or "")
        return False

    tools = getattr(agent, "tools", None)
    if tools is not None and hasattr(tools, "confirmer"):
        tools.confirmer = confirm_for_terminal_session
    agent.get_terminal_approval_status = get_terminal_approval_status
    agent._terminal_approval_state = approval_state

    def on_thinking():
        spinner.start("thinking")

    def on_tool_call(_name, _args):
        label = _tool_spinner_label(_name, _args)
        phase_state["label"] = label
        spinner.start(label)

    def on_route(event: HookEvent):
        payload = event.payload or {}
        turn_id = str(payload.get("turn_id", "")).strip()
        lane = str(payload.get("lane", "")).strip().lower()
        reason = str(payload.get("reason", "")).strip()
        route_state["lane"] = lane
        route_state["reason"] = reason
        if lane and turn_id and turn_id not in counted_route_turn_ids:
            route_counts[lane] = route_counts.get(lane, 0) + 1
            counted_route_turn_ids.add(turn_id)
        if lane and lane != "fast":
            spinner.start(f"route {lane}")

    agent.on_thinking = on_thinking
    agent.on_tool_call = on_tool_call
    agent.hooks.register("orchestrator.route", on_route)

    readline_module.set_completer(slash_completer_fn)
    readline_module.set_completer_delims(" \t")
    readline_module.parse_and_bind("set show-all-if-ambiguous on")
    readline_module.parse_and_bind("set completion-ignore-case on")
    readline_module.parse_and_bind("tab: complete")
    turn_count = 0

    click_echo_fn(f"Archon v{version} | model: {agent.config.llm.model}")
    click_echo_fn(f"Session: {session_id}")
    click_echo_fn("Type 'exit' or Ctrl-D to quit, 'reset' to clear history. Use /help for commands.\n")

    try:
        while True:
            try:
                raw_input = input_fn(make_readline_prompt_fn("you>", ansi_prompt_user))
            except (EOFError, KeyboardInterrupt):
                if turn_count > 0:
                    click_echo_fn(
                        format_session_summary_fn(
                            turn_count,
                            agent.total_input_tokens,
                            agent.total_output_tokens,
                            route_counts=route_counts,
                        )
                    )
                click_echo_fn("\nBye!")
                break

            if is_bracketed_paste_start_fn(raw_input):
                try:
                    user_input = collect_bracketed_paste_fn(
                        raw_input,
                        input_fn,
                        prompt=make_readline_prompt_fn("...>", ansi_prompt_user),
                    )
                except (EOFError, KeyboardInterrupt):
                    click_echo_fn("\n[Paste cancelled]")
                    continue
            else:
                user_input = raw_input.strip()

            if not user_input:
                continue
            if user_input == "/":
                picked = pick_slash_command_fn()
                if picked is None:
                    continue
                user_input = picked
            if is_paste_command_fn(user_input):
                click_echo_fn("Paste mode: paste your message, then end with /end (or .end).")
                try:
                    user_input = collect_paste_message_fn(
                        input_fn,
                        prompt=make_readline_prompt_fn("...>", ansi_prompt_user),
                    ).strip()
                except (EOFError, KeyboardInterrupt):
                    click_echo_fn("\n[Paste cancelled]")
                    continue
                if not user_input:
                    click_echo_fn("[Paste empty]")
                    continue
            if user_input.lower() in ("exit", "quit"):
                if turn_count > 0:
                    click_echo_fn(
                        format_session_summary_fn(
                            turn_count,
                            agent.total_input_tokens,
                            agent.total_output_tokens,
                            route_counts=route_counts,
                        )
                    )
                click_echo_fn("Bye!")
                break
            if user_input.startswith("/"):
                action, msg = handle_repl_command_fn(agent, user_input)
                if action == "reset":
                    agent.reset()
                    session_id = new_session_id_fn()
                    turn_count = 0
                    route_counts.clear()
                    counted_route_turn_ids.clear()
                    approval_state["dangerous_mode"] = False
                    approval_state["approve_next_tokens"] = 0
                    approval_state["pending_request"] = None
                    approval_state["next_approval_id"] = 1
                    approval_state["current_user_input"] = ""
                    approval_state["blocked_pending_id"] = ""
                    click_echo_fn(f"History cleared. New session: {session_id}")
                    continue
                if action in {
                    "help",
                    "status",
                    "cost",
                    "compact",
                    "context",
                    "doctor",
                    "permissions",
                    "approvals",
                    "approve",
                    "deny",
                    "approve_next",
                    "skills",
                    "plugins",
                    "model",
                    "calls",
                    "profile",
                    "mcp",
                    "jobs",
                    "job",
                }:
                    click_echo_fn(msg)
                    continue

            try:
                agent.log_label = f"terminal session={session_id}"
                route_state["lane"] = ""
                route_state["reason"] = ""
                phase_state["label"] = ""
                approval_state["current_user_input"] = user_input
                approval_state["blocked_pending_id"] = ""
                clear_expired_pending()
                pre_in = agent.total_input_tokens
                pre_out = agent.total_output_tokens
                t0 = time_time_fn()
                response = agent.run(user_input)
                elapsed = time_time_fn() - t0
                spinner.stop()
                blocked_pending_id = str(approval_state.get("blocked_pending_id") or "")
                pending = clear_expired_pending()
                approval_output = None
                if (
                    blocked_pending_id
                    and pending is not None
                    and str(pending.get("approval_id") or "") == blocked_pending_id
                    and _looks_like_safety_gate_rejection(response)
                ):
                    approval_output = _format_terminal_approval_required(
                        str(pending.get("blocked_command_preview") or "")
                    )
                turn_in = agent.total_input_tokens - pre_in
                turn_out = agent.total_output_tokens - pre_out
                turn_count += 1
                rendered_output = approval_output if approval_output is not None else (response or "")
                save_exchange_fn(session_id, user_input, rendered_output)
                if approval_output is not None:
                    click_echo_fn(approval_output)
                else:
                    click_echo_fn(format_chat_response_fn(response or ""))
                click_echo_fn(
                    format_turn_stats_fn(
                        elapsed,
                        turn_in,
                        turn_out,
                        agent.total_input_tokens,
                        agent.total_output_tokens,
                        phase_label=phase_state.get("label", ""),
                        route_lane=route_state.get("lane", ""),
                        route_reason=route_state.get("reason", ""),
                    )
                )
            except KeyboardInterrupt:
                spinner.stop()
                click_echo_fn("\n[Interrupted]")
            except Exception as e:
                spinner.stop()
                if is_model_runtime_error_fn(e):
                    provider = str(getattr(agent.llm, "provider", "") or "unknown")
                    model = str(getattr(agent.llm, "model", "") or "unknown")
                    click_echo_fn(
                        (
                            f"\n{ansi_error}Model request failed: {provider}/{model}{ansi_reset}\n"
                            f"{e}\n"
                            "Pick another model with `/model-list` and `/model-set <provider>-<model>`.\n"
                        ),
                        err=True,
                    )
                    continue
                click_echo_fn(f"\n{ansi_error}Error: {e}{ansi_reset}\n", err=True)
    finally:
        if telegram_adapter is not None:
            telegram_adapter.stop()


def _tool_spinner_label(name: str, args: dict | None) -> str:
    """Compact label for long-running tool phases in terminal UX."""
    tool = (name or "").strip().lower()
    data = args or {}
    if tool == "mcp_call":
        server = str(data.get("server", "") or "").strip().lower()
        return f"mcp {server}" if server else "mcp"
    if tool == "delegate_code_task":
        worker = str(data.get("worker", "auto") or "auto").strip().lower()
        return f"delegate {worker}"
    if tool == "worker_send":
        session_id = str(data.get("session_id", "") or "").strip()
        return f"worker send {session_id[:8]}" if session_id else "worker send"
    if tool == "worker_start":
        worker = str(data.get("worker", "auto") or "auto").strip().lower()
        return f"worker start {worker}"
    if tool.startswith("worker_"):
        return "worker task"
    if tool == "read_file":
        return "reading file"
    return f"tool {tool}" if tool else "tool"


def run_cmd(
    message: tuple[str, ...],
    *,
    make_agent_fn,
    is_model_runtime_error_fn,
    click_echo_fn,
    exit_fn,
) -> None:
    """Single-shot run command body."""
    msg = " ".join(message)
    agent = make_agent_fn()
    agent.log_label = "terminal run"
    try:
        response = agent.run(msg)
        click_echo_fn(response)
    except Exception as e:
        if is_model_runtime_error_fn(e):
            provider = str(getattr(agent.llm, "provider", "") or "unknown")
            model = str(getattr(agent.llm, "model", "") or "unknown")
            click_echo_fn(
                (
                    f"Model request failed: {provider}/{model}\n"
                    f"{e}\n"
                    "Pick another model with `/model-list` and `/model-set <provider>-<model>`."
                ),
                err=True,
            )
            exit_fn(1)
        click_echo_fn(f"Error: {e}", err=True)
        exit_fn(1)


def telegram_cmd(
    *,
    load_config_fn,
    ensure_dirs_fn,
    make_telegram_adapter_fn,
    click_echo_fn,
    exit_fn,
    version: str,
) -> None:
    """Telegram long-poll command body."""
    config = load_config_fn()
    ensure_dirs_fn()

    try:
        adapter = make_telegram_adapter_fn(config)
    except Exception as e:
        click_echo_fn(f"Telegram config error: {e}", err=True)
        exit_fn(1)

    click_echo_fn(f"Archon v{version} | Telegram adapter running")
    click_echo_fn(f"Allowed users: {len(config.telegram.allowed_user_ids)}")
    click_echo_fn("Dangerous tool actions are blocked in Telegram mode (Phase 1).")
    click_echo_fn("Press Ctrl-C to stop.\n")

    try:
        adapter.run_forever()
    except KeyboardInterrupt:
        click_echo_fn("\nStopping Telegram adapter...")


def system_cmd(*, ensure_dirs_fn, get_profile_fn, format_profile_fn, click_echo_fn) -> None:
    """System profile command body."""
    ensure_dirs_fn()
    profile = get_profile_fn(force_refresh=True)
    click_echo_fn(format_profile_fn(profile))
