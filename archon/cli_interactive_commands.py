"""Interactive/runtime command helpers for Archon CLI."""

from __future__ import annotations


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

    def on_thinking():
        spinner.start("thinking")

    def on_tool_call(_name, _args):
        spinner.start(_tool_spinner_label(_name, _args))

    agent.on_thinking = on_thinking
    agent.on_tool_call = on_tool_call

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
                    click_echo_fn(f"History cleared. New session: {session_id}")
                    continue
                if action in {"help", "model", "calls", "profile"}:
                    click_echo_fn(msg)
                    continue

            try:
                agent.log_label = f"terminal session={session_id}"
                pre_in = agent.total_input_tokens
                pre_out = agent.total_output_tokens
                t0 = time_time_fn()
                response = agent.run(user_input)
                elapsed = time_time_fn() - t0
                spinner.stop()
                turn_in = agent.total_input_tokens - pre_in
                turn_out = agent.total_output_tokens - pre_out
                turn_count += 1
                save_exchange_fn(session_id, user_input, response or "")
                click_echo_fn(format_chat_response_fn(response or ""))
                click_echo_fn(
                    format_turn_stats_fn(
                        elapsed,
                        turn_in,
                        turn_out,
                        agent.total_input_tokens,
                        agent.total_output_tokens,
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
