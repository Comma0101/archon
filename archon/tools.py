"""Tool registry and built-in tools."""

import inspect
import threading
import time
from pathlib import Path
from typing import Callable

from archon.config import Config
from archon.control.policy import resolve_profile
from archon.execution.contracts import SuspensionRequest
from archon.safety import Level, confirm
from archon.ux.tool_context import ToolContext
from archon.tooling import (
    register_call_mission_tools,
    register_call_service_tools,
    register_content_tools,
    register_filesystem_tools,
    register_memory_tools,
    register_mcp_tools,
    register_subagent_tools,
    register_setup_tools,
    register_worker_tools,
)


class ToolRegistry:
    def __init__(
        self,
        archon_source_dir: str | None = None,
        confirmer: Callable[[str, Level], bool] | None = None,
        config: Config | None = None,
        *,
        register_builtins: bool = True,
    ):
        self._init_core_state(
            archon_source_dir=archon_source_dir,
            confirmer=confirmer,
            config=config,
        )
        if register_builtins:
            self._register_builtins()

    @classmethod
    def empty(
        cls,
        *,
        archon_source_dir: str | None = None,
        confirmer: Callable[[str, Level], bool] | None = None,
        config: Config | None = None,
    ) -> "ToolRegistry":
        return cls(
            archon_source_dir=archon_source_dir,
            confirmer=confirmer,
            config=config,
            register_builtins=False,
        )

    def _init_core_state(
        self,
        *,
        archon_source_dir: str | None,
        confirmer: Callable[[str, Level], bool] | None,
        config: Config | None,
    ) -> None:
        self.tools: dict[str, dict] = {}
        self.handlers: dict[str, Callable] = {}
        self.archon_source_dir = archon_source_dir
        self.confirmer = confirmer or confirm
        self.config = config or Config()
        self.mcp_client_cls = None
        self._execute_event_handler: Callable[[str, dict], None] | None = None
        self._worker_session_affinity: dict[tuple[str, str], str] = {}
        self._session_id: str = ""

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

    def set_session_id(self, session_id: str) -> None:
        self._session_id = session_id or ""

    def _emit_ux_event(self, event: object) -> None:
        self._emit_execute_event("ux_event", {"event": event})

    def execute(self, name: str, arguments: dict) -> str | SuspensionRequest:
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
        ctx = ToolContext(
            tool_name=name,
            session_id=self._session_id,
            emit=self._emit_ux_event,
            meta={},
        )
        handler_kwargs = dict(arguments)
        heartbeat_stop = threading.Event()
        heartbeat_thread: threading.Thread | None = None
        try:
            try:
                signature = inspect.signature(handler)
            except (TypeError, ValueError):
                signature = None
            if signature is not None and "_ctx" in signature.parameters:
                handler_kwargs["_ctx"] = ctx
            if name != "shell":
                started_at = time.monotonic()

                def _heartbeat_loop() -> None:
                    from archon.ux.events import tool_running

                    while not heartbeat_stop.wait(2.0):
                        elapsed = round(time.monotonic() - started_at, 1)
                        ctx.emit(
                            tool_running(
                                tool=name,
                                session_id=ctx.session_id,
                                detail_type="heartbeat",
                                elapsed_s=elapsed,
                            )
                        )

                heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
                heartbeat_thread.start()
            result = handler(**handler_kwargs)
            if isinstance(result, SuspensionRequest):
                self._emit_execute_event(
                    "post_execute",
                    {
                        "name": name,
                        "arguments": arguments,
                        "status": "suspended",
                        "result_is_error": False,
                        "result_kind": "suspension",
                        "job_id": result.job_id,
                        "meta": dict(ctx.meta),
                    },
                )
                return result
            result_str = str(result)
            blocked = bool(ctx.meta.get("blocked"))
            self._emit_execute_event(
                "post_execute",
                {
                    "name": name,
                    "arguments": arguments,
                    "status": "blocked" if blocked else "ok",
                    "result_is_error": False,
                    "result_length": len(result_str),
                    "result_preview": result_str[:500],
                    "meta": dict(ctx.meta),
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
                    "meta": dict(ctx.meta),
                },
            )
            return result
        finally:
            heartbeat_stop.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join(timeout=0.05)

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
        register_setup_tools(self)
        register_call_service_tools(self)
        register_call_mission_tools(self)
        register_subagent_tools(self)

        register_worker_tools(self)

    @staticmethod
    def _mcp_tool_visible_for_profile(allowed: set[str]) -> bool:
        return (
            "mcp_call" in allowed
            or "mcp" in allowed
            or any(item.startswith("mcp:") for item in allowed)
        )
