"""Runtime/wiring helpers for Archon CLI."""

from __future__ import annotations

from typing import Callable


MODEL_RUNTIME_ERROR_MARKERS = (
    "503",
    "500",
    "502",
    "504",
    "429",
    "UNAVAILABLE",
    "RATE LIMIT",
    "TIMEOUT",
    "TEMPORAR",
    "TRY AGAIN",
)


def is_model_runtime_error(exc: Exception, markers: tuple[str, ...] = MODEL_RUNTIME_ERROR_MARKERS) -> bool:
    """Return True for transient provider/runtime-style model errors."""
    text = str(exc).upper()
    return any(marker in text for marker in markers)


def make_agent(
    *,
    load_config_fn,
    ensure_dirs_fn,
    llm_client_cls,
    get_source_dir_fn,
    tool_registry_cls,
    agent_cls,
    click_exception_cls,
    confirmer: Callable | None = None,
):
    """Create a fully wired agent with explicit dependency injection."""
    config = load_config_fn()
    ensure_dirs_fn()

    try:
        llm = llm_client_cls(
            provider=config.llm.provider,
            model=config.llm.model,
            api_key=config.llm.api_key,
            temperature=config.agent.temperature,
            base_url=config.llm.base_url,
        )
    except Exception as e:
        provider = config.llm.provider
        if provider == "google" and not config.llm.api_key:
            raise click_exception_cls(
                "Missing Google API key for provider 'google'. "
                "Set GEMINI_API_KEY or add [llm].api_key to ~/.config/archon/config.toml."
            ) from e
        raise click_exception_cls(
            f"Failed to initialize LLM provider '{provider}' (model={config.llm.model}): {e}"
        ) from e

    source_dir = str(get_source_dir_fn())
    tools = tool_registry_cls(
        archon_source_dir=source_dir,
        confirmer=confirmer,
        config=config,
    )
    return agent_cls(llm, tools, config)


def make_telegram_adapter(config, *, telegram_adapter_cls, make_agent_fn, headless_confirmer):
    """Create Telegram adapter from config using headless-confirm agents."""
    tg = config.telegram
    return telegram_adapter_cls(
        token=tg.token,
        allowed_user_ids=tg.allowed_user_ids,
        poll_timeout_sec=tg.poll_timeout_sec,
        agent_factory=lambda: make_agent_fn(confirmer=headless_confirmer),
    )


def print_news_result(result, *, echo_fn) -> None:
    """Render a compact CLI summary for news runs."""
    echo_fn(f"news status: {result.status}")
    if result.reason:
        echo_fn(f"reason: {result.reason}")
    if result.digest is not None:
        echo_fn(
            f"digest: {result.digest.date_iso} | items={result.digest.item_count} "
            f"| fallback={result.digest.used_fallback}"
        )
