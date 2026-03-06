"""History command helpers for Archon CLI."""

from __future__ import annotations


def history_list_cmd(
    limit: int,
    *,
    ensure_dirs_fn,
    list_sessions_fn,
    strftime_fn,
    localtime_fn,
    echo_fn,
) -> None:
    """List recent chat sessions."""
    ensure_dirs_fn()
    sessions = list_sessions_fn(limit=limit)
    if not sessions:
        echo_fn("No saved sessions yet.")
        return
    for session in sessions:
        ts = strftime_fn("%Y-%m-%d %H:%M", localtime_fn(session["modified"]))
        echo_fn(f"  {session['session_id']}  {session['messages']} msgs  {ts}")


def history_show_cmd(
    session_id: str,
    *,
    ensure_dirs_fn,
    load_session_fn,
    echo_fn,
    ansi_prompt_user: str,
    ansi_prompt_archon: str,
    ansi_reset: str,
) -> None:
    """Show messages from one session."""
    ensure_dirs_fn()
    messages = load_session_fn(session_id)
    if not messages:
        echo_fn(f"Session not found: {session_id}")
        return
    for msg in messages:
        role = msg.get("role", "?")
        content = msg.get("content", "")
        if role == "user":
            echo_fn(f"{ansi_prompt_user}you>{ansi_reset} {content}")
        else:
            echo_fn(f"{ansi_prompt_archon}archon>{ansi_reset} {content}\n")


def history_delete_cmd(
    session_id: str,
    *,
    ensure_dirs_fn,
    delete_session_fn,
    echo_fn,
) -> None:
    """Delete one session."""
    ensure_dirs_fn()
    if delete_session_fn(session_id):
        echo_fn(f"Deleted session: {session_id}")
    else:
        echo_fn(f"Session not found: {session_id}")
