"""Memory command helpers for Archon CLI."""

from __future__ import annotations


def memory_search_cmd(
    query: str,
    *,
    ensure_dirs_fn,
    memory_search_fn,
    echo_fn,
    ansi_path: str,
    ansi_reset: str,
) -> None:
    """Search memory files and render CLI output."""
    ensure_dirs_fn()
    results = memory_search_fn(query)
    if not results:
        echo_fn("No results found.")
        return
    for filepath, linenum, content in results:
        echo_fn(f"{ansi_path}{filepath}:{linenum}{ansi_reset}: {content}")


def memory_list_cmd(*, ensure_dirs_fn, list_files_fn, echo_fn) -> None:
    """List all memory files."""
    ensure_dirs_fn()
    files = list_files_fn()
    if not files:
        echo_fn("No memory files yet.")
        return
    for path in files:
        echo_fn(path)


def memory_read_cmd(path: str, *, ensure_dirs_fn, memory_read_fn, echo_fn) -> None:
    """Read one memory file."""
    ensure_dirs_fn()
    content = memory_read_fn(path)
    if not content:
        echo_fn(f"Not found: {path}")
        return
    echo_fn(content)
