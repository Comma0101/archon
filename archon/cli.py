"""Click CLI: chat, run, system, self, memory commands."""

import json
import os
import readline
import sys
import subprocess
import time
from pathlib import Path
from typing import Callable

import click

from archon import __version__
from archon.config import ACTIVITY_DIR, CONFIG_DIR, load_config, ensure_dirs
from archon.system import get_profile, format_profile
from archon.introspect import format_self_awareness, get_source_dir
from archon.memory import read as memory_read, search as memory_search, list_files
from archon.llm import LLMClient
from archon.tools import ToolRegistry
from archon.agent import Agent
from archon.adapters.telegram import TelegramAdapter, headless_confirmer
from archon.history import new_session_id, save_exchange, list_sessions, load_session, delete_session
from archon.news.runner import run_news
from archon.news.state import load_news_state, news_state_path
from archon.cli_history_commands import (
    history_delete_cmd as _history_delete_cmd_impl,
    history_list_cmd as _history_list_cmd_impl,
    history_show_cmd as _history_show_cmd_impl,
)
from archon.cli_activity_commands import (
    activity_reset_impl as _activity_reset_cmd_impl,
    activity_status_impl as _activity_status_cmd_impl,
    activity_summary_impl as _activity_summary_cmd_impl,
)
from archon.cli_interactive_commands import (
    chat_cmd as _chat_cmd_impl,
    run_cmd as _run_cmd_impl,
    system_cmd as _system_cmd_impl,
    telegram_cmd as _telegram_cmd_impl,
)
from archon.cli_memory_commands import (
    memory_list_cmd as _memory_list_cmd_impl,
    memory_read_cmd as _memory_read_cmd_impl,
    memory_search_cmd as _memory_search_cmd_impl,
)
from archon.cli_news_commands import (
    news_preview_cmd as _news_preview_cmd_impl,
    news_run_cmd as _news_run_cmd_impl,
    news_status_cmd as _news_status_cmd_impl,
)
from archon.cli_self_commands import (
    self_info_cmd as _self_info_cmd_impl,
    self_recover_cmd as _self_recover_cmd_impl,
)
from archon.cli_commands import (
    MODEL_CATALOG as _MODEL_CATALOG,
    build_slash_commands as _build_slash_commands_impl,
    build_model_set_subvalues as _build_model_set_subvalues_impl,
    build_slash_subvalues as _build_slash_subvalues,
    pick_slash_command as _pick_slash_command_impl,
    run_picker as _run_picker_impl,
    slash_completer as _slash_completer_impl,
)
from archon.cli_input import (
    BRACKETED_PASTE_END,
    BRACKETED_PASTE_START,
    PASTE_END_MARKERS,
    collect_bracketed_paste as _collect_bracketed_paste_impl,
    collect_paste_message as _collect_paste_message_impl,
    is_bracketed_paste_start as _is_bracketed_paste_start_impl,
    is_paste_command as _is_paste_command_impl,
)
from archon.slash_palette import read_interactive_input as _read_interactive_input_impl
from archon.cli_repl_commands import (
    handle_calls_command as _handle_calls_command_impl,
    handle_model_command as _handle_model_command_impl,
    handle_model_list_command as _handle_model_list_command_impl,
    handle_model_set_command as _handle_model_set_command_impl,
    handle_profile_command as _handle_profile_command_impl,
    handle_repl_command as _handle_repl_command_impl,
    resolve_provider_credentials as _resolve_provider_credentials_impl,
    set_calls_enabled_in_toml as _set_calls_enabled_in_toml_impl,
)
from archon.cli_runtime import (
    is_model_runtime_error as _is_model_runtime_error_impl,
    make_agent as _make_agent_impl,
    make_telegram_adapter as _make_telegram_adapter_impl,
    print_news_result as _print_news_result_impl,
)
from archon.cli_ui import (
    ANSI_DIM,
    ANSI_ERROR,
    ANSI_PATH,
    ANSI_PROMPT_ARCHON,
    ANSI_PROMPT_USER,
    ANSI_RESET,
    _Spinner,
    _begin_streamed_chat_response,
    _end_streamed_chat_response,
    _format_chat_response,
    _format_streamed_chat_chunk,
    _format_session_summary,
    _format_turn_stats,
    _make_readline_prompt,
)

_SLASH_COMMANDS = _build_slash_commands_impl()
_SLASH_NAMES = [name for name, _ in _SLASH_COMMANDS]


def _build_model_set_subvalues() -> list[tuple[str, str]]:
    """Build sub-values for /model-set from _MODEL_CATALOG."""
    return _build_model_set_subvalues_impl(_MODEL_CATALOG)


def _refresh_slash_subvalues(config=None) -> dict[str, list[tuple[str, str]]]:
    """Refresh slash sub-values from runtime config when available."""
    global _SLASH_SUBVALUES
    _SLASH_SUBVALUES = _build_slash_subvalues(_MODEL_CATALOG, config)
    return _SLASH_SUBVALUES


_SLASH_SUBVALUES: dict[str, list[tuple[str, str]]] = _refresh_slash_subvalues()


def _slash_completer(text, state):
    """readline completer for slash commands."""
    try:
        line_buffer = readline.get_line_buffer()
    except Exception:
        line_buffer = ""
    return _slash_completer_impl(
        text,
        state,
        _SLASH_NAMES,
        _SLASH_SUBVALUES,
        line_buffer,
    )


def _run_picker(items: list[tuple[str, str]], label_width: int = 10) -> str | None:
    """Interactive arrow-key picker. Returns selected item name or None."""
    return _run_picker_impl(items, label_width=label_width)


def _pick_slash_command(query: str | None = None) -> str | None:
    """Interactive two-level command picker with optional sub-value selection."""
    return _pick_slash_command_impl(
        run_picker_fn=_run_picker,
        slash_commands=_SLASH_COMMANDS,
        slash_subvalues=_SLASH_SUBVALUES,
        query=query,
    )


def _is_paste_command(text: str) -> bool:
    return _is_paste_command_impl(text)


def _collect_paste_message(read_line, prompt: str) -> str:
    return _collect_paste_message_impl(read_line, prompt, end_markers=PASTE_END_MARKERS)


def _is_bracketed_paste_start(text: str) -> bool:
    return _is_bracketed_paste_start_impl(text, start_marker=BRACKETED_PASTE_START)


def _collect_bracketed_paste(first_line: str, read_line, prompt: str) -> str:
    return _collect_bracketed_paste_impl(
        first_line,
        read_line,
        prompt,
        start_marker=BRACKETED_PASTE_START,
        end_marker=BRACKETED_PASTE_END,
    )


def _read_interactive_input(prompt: str, fallback_read_fn) -> tuple[str, bool]:
    return _read_interactive_input_impl(
        prompt=prompt,
        fallback_read_fn=fallback_read_fn,
        readline_module=readline,
        slash_commands=_SLASH_COMMANDS,
        slash_subvalues=_SLASH_SUBVALUES,
    )


def _supports_interactive_prompts() -> bool:
    for stream in (sys.stdin, sys.stdout):
        fileno = getattr(stream, "fileno", None)
        if not callable(fileno):
            return False
        try:
            fd = fileno()
        except (AttributeError, OSError, ValueError):
            return False
        if not os.isatty(fd):
            return False
    return True


def _make_runtime_prompt(label: str, color_ansi: str) -> str:
    if not _supports_interactive_prompts():
        return f"{label} "
    return _make_readline_prompt(label, color_ansi)


def _handle_model_command(agent: Agent, text: str) -> tuple[bool, str]:
    raw = (text or "").strip()
    lowered = raw.lower()
    if lowered.startswith("/model set"):
        remainder = raw[len("/model") :].lstrip()
        alias_text = f"/model-{remainder}" if remainder else "/model-set"
        return _handle_model_set_command(agent, alias_text)
    return _handle_model_command_impl(agent, text)


def _handle_model_list_command(text: str) -> tuple[bool, str]:
    return _handle_model_list_command_impl(text, _MODEL_CATALOG)


def _handle_model_set_command(agent: Agent, text: str) -> tuple[bool, str]:
    return _handle_model_set_command_impl(
        agent,
        text,
        llm_factory=LLMClient,
        resolve_provider_credentials_fn=_resolve_provider_credentials,
    )


def _resolve_provider_credentials(llm_cfg, provider: str) -> tuple[str, str]:
    return _resolve_provider_credentials_impl(
        llm_cfg,
        provider,
        env_getter=os.environ.get,
    )


def _set_calls_enabled_in_toml(text: str, enabled: bool) -> str:
    return _set_calls_enabled_in_toml_impl(text, enabled)


def _set_calls_enabled_config(enabled: bool, config_path: Path | None = None) -> str:
    """Persist the calls feature flag in the user's config.toml."""
    path = Path(config_path) if config_path is not None else (CONFIG_DIR / "config.toml")
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    updated = _set_calls_enabled_in_toml(existing, enabled)
    path.write_text(updated, encoding="utf-8")
    return str(path)


def _handle_calls_command(agent: Agent, text: str) -> tuple[bool, str]:
    return _handle_calls_command_impl(
        agent,
        text,
        load_config_fn=load_config,
        set_calls_enabled_config_fn=_set_calls_enabled_config,
    )


def _handle_profile_command(agent: Agent, text: str) -> tuple[bool, str]:
    return _handle_profile_command_impl(agent, text)


def _handle_repl_command(agent: Agent, text: str) -> tuple[str | None, str]:
    return _handle_repl_command_impl(
        agent,
        text,
        slash_commands=_SLASH_COMMANDS,
        handle_calls_command_fn=_handle_calls_command,
        handle_profile_command_fn=_handle_profile_command,
        handle_model_list_command_fn=_handle_model_list_command,
        handle_model_set_command_fn=_handle_model_set_command,
        handle_model_command_fn=_handle_model_command,
    )


def _is_model_runtime_error(exc: Exception) -> bool:
    return _is_model_runtime_error_impl(exc)


def _make_agent(confirmer: Callable | None = None) -> Agent:
    return _make_agent_impl(
        load_config_fn=load_config,
        ensure_dirs_fn=ensure_dirs,
        llm_client_cls=LLMClient,
        get_source_dir_fn=get_source_dir,
        tool_registry_cls=ToolRegistry,
        agent_cls=Agent,
        click_exception_cls=click.ClickException,
        confirmer=confirmer,
    )


def _make_telegram_adapter(config) -> TelegramAdapter:
    return _make_telegram_adapter_impl(
        config,
        telegram_adapter_cls=TelegramAdapter,
        make_agent_fn=_make_agent,
        headless_confirmer=headless_confirmer,
    )


def _print_news_result(result):
    _print_news_result_impl(result, echo_fn=click.echo)


@click.group(invoke_without_command=True)
@click.version_option(version=__version__)
@click.pass_context
def main(ctx):
    """Archon: Lightweight self-aware AI agent for Arch Linux."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(chat)


@main.command()
def chat():
    _chat_cmd_impl(
        make_agent_fn=_make_agent,
        make_telegram_adapter_fn=_make_telegram_adapter,
        new_session_id_fn=new_session_id,
        save_exchange_fn=save_exchange,
        refresh_slash_subvalues_fn=_refresh_slash_subvalues,
        slash_completer_fn=_slash_completer,
        pick_slash_command_fn=_pick_slash_command,
        is_bracketed_paste_start_fn=_is_bracketed_paste_start,
        collect_bracketed_paste_fn=_collect_bracketed_paste,
        is_paste_command_fn=_is_paste_command,
        collect_paste_message_fn=_collect_paste_message,
        handle_repl_command_fn=_handle_repl_command,
        is_model_runtime_error_fn=_is_model_runtime_error,
        format_session_summary_fn=_format_session_summary,
        format_chat_response_fn=_format_chat_response,
        format_turn_stats_fn=_format_turn_stats,
        make_readline_prompt_fn=_make_runtime_prompt,
        spinner_cls=_Spinner,
        begin_streamed_chat_response_fn=_begin_streamed_chat_response,
        format_streamed_chat_chunk_fn=_format_streamed_chat_chunk,
        end_streamed_chat_response_fn=_end_streamed_chat_response,
        ansi_prompt_user=ANSI_PROMPT_USER,
        ansi_error=ANSI_ERROR,
        ansi_reset=ANSI_RESET,
        click_echo_fn=click.echo,
        stream_write_fn=sys.stdout.write,
        stream_flush_fn=sys.stdout.flush,
        input_fn=input,
        readline_module=readline,
        time_time_fn=time.time,
        version=__version__,
        read_interactive_input_fn=_read_interactive_input,
    )


@main.command()
@click.argument("message", nargs=-1, required=True)
def run(message):
    _run_cmd_impl(
        message,
        make_agent_fn=_make_agent,
        is_model_runtime_error_fn=_is_model_runtime_error,
        click_echo_fn=click.echo,
        exit_fn=sys.exit,
    )


@main.command()
def telegram():
    _telegram_cmd_impl(
        load_config_fn=load_config,
        ensure_dirs_fn=ensure_dirs,
        make_telegram_adapter_fn=_make_telegram_adapter,
        click_echo_fn=click.echo,
        exit_fn=sys.exit,
        version=__version__,
    )


@main.command("system")
def system_cmd():
    _system_cmd_impl(
        ensure_dirs_fn=ensure_dirs,
        get_profile_fn=get_profile,
        format_profile_fn=format_profile,
        click_echo_fn=click.echo,
    )


@main.group("news")
def news_group():
    """AI news briefing commands."""
    pass


@news_group.command("run")
@click.option("--force", is_flag=True, help="Force run even if already run today.")
@click.option("--no-send", is_flag=True, help="Build digest but do not send it to Telegram.")
def news_run_cmd(force, no_send):
    _news_run_cmd_impl(
        force,
        no_send,
        load_config_fn=load_config,
        ensure_dirs_fn=ensure_dirs,
        run_news_fn=run_news,
        print_news_result_fn=_print_news_result,
        exit_fn=sys.exit,
    )


@news_group.command("preview")
@click.option("--force", is_flag=True, help="Ignore the daily run gate in preview mode.")
def news_preview_cmd(force):
    _news_preview_cmd_impl(
        force,
        load_config_fn=load_config,
        ensure_dirs_fn=ensure_dirs,
        run_news_fn=run_news,
        echo_fn=click.echo,
        print_news_result_fn=_print_news_result,
    )


@news_group.command("status")
def news_status_cmd():
    _news_status_cmd_impl(
        ensure_dirs_fn=ensure_dirs,
        load_news_state_fn=load_news_state,
        news_state_path_fn=news_state_path,
        json_dumps_fn=json.dumps,
        echo_fn=click.echo,
    )


@main.group("memory")
def memory_group():
    """Manage persistent memory."""
    pass


@main.group("activity")
def activity_group():
    """Inspect activity context and stored snapshots."""
    pass


@activity_group.command("status")
def activity_status_cmd():
    cfg = load_config()
    ensure_dirs()
    _activity_status_cmd_impl(
        config=cfg.activity,
        activity_dir=ACTIVITY_DIR,
        echo_fn=click.echo,
    )


@activity_group.command("summary")
def activity_summary_cmd():
    cfg = load_config()
    ensure_dirs()
    _activity_summary_cmd_impl(
        config=cfg.activity,
        activity_dir=ACTIVITY_DIR,
        echo_fn=click.echo,
    )


@activity_group.command("reset")
def activity_reset_cmd():
    ensure_dirs()
    _activity_reset_cmd_impl(
        activity_dir=ACTIVITY_DIR,
        echo_fn=click.echo,
    )


@memory_group.command("search")
@click.argument("query")
def memory_search_cmd(query):
    _memory_search_cmd_impl(
        query,
        ensure_dirs_fn=ensure_dirs,
        memory_search_fn=memory_search,
        echo_fn=click.echo,
        ansi_path=ANSI_PATH,
        ansi_reset=ANSI_RESET,
    )


@memory_group.command("list")
def memory_list_cmd():
    _memory_list_cmd_impl(
        ensure_dirs_fn=ensure_dirs,
        list_files_fn=list_files,
        echo_fn=click.echo,
    )


@memory_group.command("read")
@click.argument("path")
def memory_read_cmd(path):
    _memory_read_cmd_impl(
        path,
        ensure_dirs_fn=ensure_dirs,
        memory_read_fn=memory_read,
        echo_fn=click.echo,
    )


@main.group("history")
def history_group():
    """Conversation history commands."""
    pass


@history_group.command("list")
@click.option("--limit", default=20, help="Max sessions to show.")
def history_list_cmd(limit):
    _history_list_cmd_impl(
        limit,
        ensure_dirs_fn=ensure_dirs,
        list_sessions_fn=list_sessions,
        strftime_fn=time.strftime,
        localtime_fn=time.localtime,
        echo_fn=click.echo,
    )


@history_group.command("show")
@click.argument("session_id")
def history_show_cmd(session_id):
    _history_show_cmd_impl(
        session_id,
        ensure_dirs_fn=ensure_dirs,
        load_session_fn=load_session,
        echo_fn=click.echo,
        ansi_prompt_user=ANSI_PROMPT_USER,
        ansi_prompt_archon=ANSI_PROMPT_ARCHON,
        ansi_reset=ANSI_RESET,
    )


@history_group.command("delete")
@click.argument("session_id")
def history_delete_cmd(session_id):
    _history_delete_cmd_impl(
        session_id,
        ensure_dirs_fn=ensure_dirs,
        delete_session_fn=delete_session,
        echo_fn=click.echo,
    )


@main.group("self", invoke_without_command=True)
@click.pass_context
def self_group(ctx):
    """Self-awareness commands."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(self_info)


@self_group.command("info")
@click.pass_context
def self_info(ctx):
    _self_info_cmd_impl(
        format_self_awareness_fn=format_self_awareness,
        click_echo_fn=click.echo,
    )


@self_group.command("recover")
def self_recover():
    _self_recover_cmd_impl(
        get_source_dir_fn=get_source_dir,
        click_echo_fn=click.echo,
        click_confirm_fn=click.confirm,
        subprocess_run_fn=subprocess.run,
        called_process_error_cls=subprocess.CalledProcessError,
        exit_fn=sys.exit,
    )


if __name__ == "__main__":
    main()
