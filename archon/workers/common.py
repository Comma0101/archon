"""Shared helper utilities used across worker adapters and worker result formatting."""


def truncate_inline(text: str, max_chars: int) -> str:
    """Short single-line truncation style used by worker adapters."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."


def truncate_report(text: str, max_chars: int) -> str:
    """Verbose truncation style used in worker result rendering."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n... (truncated, {len(text) - max_chars} chars omitted)"


def first_nonempty_line(*values: str) -> str:
    for value in values:
        if not value:
            continue
        for line in value.splitlines():
            if line.strip():
                return line.strip()
    return ""


def summarize_cli_run(
    worker_display: str,
    status: str,
    exit_code: int,
    final_message: str,
    stderr: str,
) -> str:
    if status == "ok":
        if final_message.strip():
            first_line = final_message.strip().splitlines()[0]
            return truncate_inline(first_line, 240)
        return f"Delegated {worker_display} task completed."
    if stderr.strip():
        first_line = stderr.strip().splitlines()[0]
        return truncate_inline(f"{worker_display} exited {exit_code}: {first_line}", 240)
    return f"{worker_display} exited with code {exit_code}"
