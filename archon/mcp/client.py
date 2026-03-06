"""Minimal read-only MCP client scaffolding."""

from __future__ import annotations

from typing import Callable

from archon.config import MCPConfig, MCPServerConfig


TransportFn = Callable[[MCPServerConfig, dict], object]


class MCPClient:
    def __init__(self, config: MCPConfig):
        self.config = config

    def invoke(
        self,
        server_name: str,
        payload: dict,
        *,
        transport_fn: TransportFn,
    ) -> dict:
        server_key = str(server_name or "").strip()
        server = self.config.servers.get(server_key)
        if server is None or not server.enabled:
            return {"error": f"Error: MCP server '{server_key}' is not available"}
        if str(server.mode or "").strip().lower() != "read_only":
            return {"error": f"Error: MCP server '{server_key}' must be read_only"}

        raw = transport_fn(server, payload or {})
        content = self._cap_output(raw)
        return {
            "server": server_key,
            "mode": "read_only",
            "content": content,
            "truncated": str(raw or "") != content,
        }

    def _cap_output(self, raw: object) -> str:
        text = str(raw or "")
        limit = max(1, int(getattr(self.config, "result_max_chars", 2000)))
        if len(text) <= limit:
            return text
        return text[: max(1, limit - 3)].rstrip() + "..."
