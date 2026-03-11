"""Tool registry and built-in tools."""

from pathlib import Path
from typing import Callable

from archon.config import Config
from archon.control.policy import resolve_profile
from archon.safety import Level, confirm
from archon.tooling import (
    register_call_mission_tools,
    register_call_service_tools,
    register_content_tools,
    register_filesystem_tools,
    register_memory_tools,
    register_mcp_tools,
    register_worker_tools,
)


class ToolRegistry:
    def __init__(
        self,
        archon_source_dir: str | None = None,
        confirmer: Callable[[str, Level], bool] | None = None,
        config: Config | None = None,
    ):
        self.tools: dict[str, dict] = {}
        self.handlers: dict[str, Callable] = {}
        self.archon_source_dir = archon_source_dir
        self.confirmer = confirmer or confirm
        self.config = config or Config()
        self.mcp_client_cls = None
        self._execute_event_handler: Callable[[str, dict], None] | None = None
        self._worker_session_affinity: dict[tuple[str, str], str] = {}
        self._register_builtins()

    def register(self, name: str, description: str,
                 parameters: dict, handler: Callable):
        self.tools[name] = {
            "name": name,
            "description": description,
            "input_schema": {
                "type": "object",
                "properties": parameters.get("properties", {}),
                "required": parameters.get("required", []),
            },
        }
        self.handlers[name] = handler

    def get_schemas(self) -> list[dict]:
        return list(self.tools.values())

    def get_schemas_for_profile(
        self,
        config: Config | None = None,
        *,
        profile_name: str = "default",
    ) -> list[dict]:
        cfg = config or self.config
        _resolved_name, profile = resolve_profile(cfg, profile_name=profile_name)
        allowed = {
            str(item or "").strip().lower()
            for item in getattr(profile, "allowed_tools", ())
            if str(item or "").strip()
        }
        if not allowed or "*" in allowed:
            return self.get_schemas()

        visible: list[dict] = []
        for schema in self.get_schemas():
            name = str(schema.get("name", "") or "").strip().lower()
            if not name:
                continue
            if name in allowed:
                visible.append(schema)
                continue
            if name == "mcp_call" and self._mcp_tool_visible_for_profile(allowed):
                visible.append(schema)
        return visible

    def set_execute_event_handler(self, handler: Callable[[str, dict], None] | None) -> None:
        self._execute_event_handler = handler

    def _emit_execute_event(self, kind: str, payload: dict) -> None:
        if self._execute_event_handler is None:
            return
        try:
            self._execute_event_handler(kind, payload)
        except Exception:
            # Event handler must never affect tool execution semantics.
            return

    def execute(self, name: str, arguments: dict) -> str:
        self._emit_execute_event(
            "pre_execute",
            {"name": name, "arguments": arguments},
        )
        handler = self.handlers.get(name)
        if not handler:
            result = f"Error: Unknown tool '{name}'"
            self._emit_execute_event(
                "post_execute",
                {
                    "name": name,
                    "arguments": arguments,
                    "status": "unknown_tool",
                    "result_is_error": True,
                },
            )
            return result
        try:
            result = handler(**arguments)
            self._emit_execute_event(
                "post_execute",
                {
                    "name": name,
                    "arguments": arguments,
                    "status": "ok",
                    "result_is_error": False,
                    "result_length": len(str(result)),
                },
            )
            return result
        except Exception as e:
            result = f"Error: {type(e).__name__}: {e}"
            self._emit_execute_event(
                "post_execute",
                {
                    "name": name,
                    "arguments": arguments,
                    "status": "error",
                    "result_is_error": True,
                    "result_length": len(result),
                    "error_type": type(e).__name__,
                    "error": str(e),
                },
            )
            return result

    def _worker_affinity_key(self, worker: str, repo_path: str) -> tuple[str, str]:
        worker_key = (worker or "").strip().lower()
        try:
            repo_key = str(Path(repo_path).expanduser().resolve())
        except Exception:
            repo_key = str(repo_path)
        return worker_key, repo_key

    def _set_worker_session_affinity(self, session_id: str, repo_path: str, *workers: str) -> None:
        for worker in workers:
            worker_key, repo_key = self._worker_affinity_key(worker, repo_path)
            if not worker_key:
                continue
            self._worker_session_affinity[(worker_key, repo_key)] = session_id

    def _get_worker_session_affinity(self, worker: str, repo_path: str) -> str:
        worker_key, repo_key = self._worker_affinity_key(worker, repo_path)
        return self._worker_session_affinity.get((worker_key, repo_key), "")

    def _clear_worker_session_affinity(self, session_id: str) -> None:
        for key, value in list(self._worker_session_affinity.items()):
            if value == session_id:
                self._worker_session_affinity.pop(key, None)

    def _register_builtins(self):
        register_filesystem_tools(self)
        register_memory_tools(self)
        register_content_tools(self)
        register_mcp_tools(self)
        register_call_service_tools(self)
        register_call_mission_tools(self)

        register_worker_tools(self)

    @staticmethod
    def _mcp_tool_visible_for_profile(allowed: set[str]) -> bool:
        return (
            "mcp_call" in allowed
            or "mcp" in allowed
            or any(item.startswith("mcp:") for item in allowed)
        )
