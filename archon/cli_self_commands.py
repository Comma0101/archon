"""Self command helpers for Archon CLI."""

from __future__ import annotations


def self_info_cmd(*, format_self_awareness_fn, click_echo_fn) -> None:
    """Render self-awareness source map."""
    click_echo_fn(format_self_awareness_fn())


def self_recover_cmd(
    *,
    get_source_dir_fn,
    click_echo_fn,
    click_confirm_fn,
    subprocess_run_fn,
    called_process_error_cls,
    exit_fn,
) -> None:
    """Reset project to last known-good state."""
    source_dir = get_source_dir_fn()
    project_dir = source_dir.parent

    click_echo_fn(f"Project dir: {project_dir}")
    click_echo_fn("This will discard all uncommitted changes.")
    if not click_confirm_fn("Proceed?"):
        return

    try:
        result = subprocess_run_fn(
            ["git", "-C", str(project_dir), "describe", "--tags", "--abbrev=0"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            target = result.stdout.strip()
            click_echo_fn(f"Resetting to tag: {target}")
        else:
            click_echo_fn("No tags found. Resetting to last commit.")

        subprocess_run_fn(
            ["git", "-C", str(project_dir), "checkout", "--", "."],
            check=True,
        )
        click_echo_fn("Recovery complete.")
    except called_process_error_cls as e:
        click_echo_fn(f"Recovery failed: {e}", err=True)
        exit_fn(1)
