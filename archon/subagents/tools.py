"""Native subagent tool registration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from archon.config import Config, resolve_tier_model
from archon.execution.history_shaping import truncate_text_for_history
from archon.llm import LLMClient

from .types import get_subagent_type

if TYPE_CHECKING:
    from .runner import SubagentResult

SubagentRunner = None


def register_subagent_tools(registry) -> None:
    """Register the parent-facing spawn_subagent tool."""

    def spawn_subagent(
        task: str,
        type: str = "explore",
        context: str = "",
        _ctx=None,
    ) -> str:
        task_text = str(task or "").strip()
        if not task_text:
            return "Error: Task cannot be empty."

        try:
            subagent_type = get_subagent_type(type)
        except KeyError:
            return f"Error: Unknown subagent type: {type}"

        cfg = _resolve_registry_config(registry)
        from .registry import build_subagent_registry

        runner_cls = SubagentRunner
        if runner_cls is None:
            from .runner import SubagentRunner as runner_cls

        child_registry = build_subagent_registry(
            subagent_type=subagent_type,
            archon_source_dir=getattr(registry, "archon_source_dir", None),
            confirmer=getattr(registry, "confirmer", None),
            config=cfg,
        )
        llm = LLMClient(
            provider=str(getattr(cfg.llm, "provider", "") or ""),
            model=resolve_tier_model(cfg, subagent_type.tier),
            api_key=str(getattr(cfg.llm, "api_key", "") or ""),
            temperature=float(getattr(cfg.agent, "temperature", 0.3)),
            base_url=str(getattr(cfg.llm, "base_url", "") or ""),
        )
        provider = str(getattr(llm, "provider", "") or getattr(cfg.llm, "provider", "") or "").strip()
        model = str(getattr(llm, "model", "") or resolve_tier_model(cfg, subagent_type.tier)).strip()
        runner = runner_cls(
            llm=llm,
            tools=child_registry,
            config=cfg,
            subagent_type=subagent_type.name,
            max_iterations=getattr(cfg.agent, "max_iterations", None),
            wall_clock_timeout_sec=getattr(cfg.agent, "wall_clock_timeout_sec", None),
            llm_retry_attempts=getattr(cfg.agent, "llm_retry_attempts", None),
            llm_request_timeout_sec=getattr(cfg.agent, "llm_request_timeout_sec", None),
            tool_result_max_chars=getattr(cfg.agent, "tool_result_max_chars", None),
            tool_result_worker_max_chars=getattr(cfg.agent, "tool_result_worker_max_chars", None),
        )
        result = runner.run(task_text, context=str(context or "").strip())
        registry._emit_execute_event(
            "subagent_usage",
            {
                "source": f"subagent:{subagent_type.name}",
                "subagent_type": subagent_type.name,
                "status": result.status,
                "iterations_used": result.iterations_used,
                "provider": provider,
                "model": model,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
            },
        )
        return _format_subagent_result(
            subagent_type=subagent_type.name,
            result=result,
            max_iterations=int(getattr(cfg.agent, "max_iterations", 1) or 1),
            max_result_chars=int(getattr(cfg.agent, "tool_result_max_chars", 3000) or 3000),
        )

    registry.register(
        "spawn_subagent",
        'Run a bounded native subagent with a fresh context window. '
        'Use spawn_subagent(type="explore") for bounded exploration/research, '
        'use spawn_subagent(type="general") for bounded in-process task execution, '
        "and use delegate_code_task for heavy, sandboxed, or durable worker work.",
        {
            "properties": {
                "task": {"type": "string"},
                "type": {"type": "string", "default": "explore"},
                "context": {"type": "string", "default": ""},
            },
            "required": ["task"],
        },
        spawn_subagent,
    )


def _resolve_registry_config(registry) -> Config:
    config = getattr(registry, "config", None)
    if isinstance(config, Config):
        return config
    return Config()


def _format_subagent_result(
    *,
    subagent_type: str,
    result: "SubagentResult",
    max_iterations: int,
    max_result_chars: int,
) -> str:
    lines = [
        f"subagent_type: {subagent_type}",
        f"status: {result.status}",
        f"iterations: {result.iterations_used}/{max_iterations}",
        f"tokens: {result.input_tokens} in, {result.output_tokens} out",
    ]
    text = str(result.text or "").strip()
    if text:
        lines.extend(["", truncate_text_for_history(text, max_result_chars)])
    return "\n".join(lines)


__all__ = ["register_subagent_tools", "SubagentRunner"]
