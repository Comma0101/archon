"""News command helpers for Archon CLI."""

from __future__ import annotations


def news_run_cmd(
    force: bool,
    no_send: bool,
    *,
    load_config_fn,
    ensure_dirs_fn,
    run_news_fn,
    print_news_result_fn,
    exit_fn,
) -> None:
    """Run the daily AI news pipeline and exit non-zero on errors."""
    config = load_config_fn()
    ensure_dirs_fn()
    result = run_news_fn(config, force=force, send_telegram=not no_send)
    print_news_result_fn(result)
    if result.status == "error":
        exit_fn(1)


def news_preview_cmd(
    force: bool,
    *,
    load_config_fn,
    ensure_dirs_fn,
    run_news_fn,
    echo_fn,
    print_news_result_fn,
) -> None:
    """Preview news digest output without sending."""
    config = load_config_fn()
    ensure_dirs_fn()
    result = run_news_fn(config, force=force, send_telegram=False, preview_only=True)
    if result.digest is not None:
        echo_fn(result.digest.markdown)
        return
    print_news_result_fn(result)


def news_status_cmd(
    *,
    ensure_dirs_fn,
    load_news_state_fn,
    news_state_path_fn,
    json_dumps_fn,
    echo_fn,
) -> None:
    """Render JSON status payload for news pipeline state."""
    ensure_dirs_fn()
    state = load_news_state_fn()
    payload = {
        "state_file": str(news_state_path_fn()),
        "state": state,
    }
    echo_fn(json_dumps_fn(payload, indent=2))
