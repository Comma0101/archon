"""Fresh filtered registries for native subagents."""

from __future__ import annotations

from collections.abc import Callable

from archon.config import Config
from archon.safety import Level
from archon.tools import ToolRegistry
from archon.tooling import register_content_tools, register_filesystem_tools

from .types import SubagentType, get_subagent_type


def build_subagent_registry(
    *,
    subagent_type: SubagentType | str,
    archon_source_dir: str | None = None,
    confirmer: Callable[[str, Level], bool] | None = None,
    config: Config | None = None,
) -> ToolRegistry:
    subagent = _resolve_subagent_type(subagent_type)
    registry = ToolRegistry.empty(
        archon_source_dir=archon_source_dir,
        confirmer=_wrap_explore_confirmer(confirmer) if subagent.name == "explore" else confirmer,
        config=config,
    )
    register_filesystem_tools(registry)
    if subagent.name == "general":
        register_content_tools(registry)
    _prune_registry_tools(registry, set(subagent.allowed_tools))
    return registry


def _resolve_subagent_type(subagent_type: SubagentType | str) -> SubagentType:
    if isinstance(subagent_type, SubagentType):
        return subagent_type
    return get_subagent_type(subagent_type)


def _wrap_explore_confirmer(
    confirmer: Callable[[str, Level], bool] | None,
) -> Callable[[str, Level], bool]:
    def _wrapped(command: str, level: Level) -> bool:
        if level in {Level.DANGEROUS, Level.FORBIDDEN}:
            return False
        if confirmer is None:
            return True
        return confirmer(command, level)

    return _wrapped


def _prune_registry_tools(registry: ToolRegistry, allowed_tools: set[str]) -> None:
    for name in list(registry.tools):
        if name not in allowed_tools:
            registry.tools.pop(name, None)
            registry.handlers.pop(name, None)


__all__ = ["build_subagent_registry"]
