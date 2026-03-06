"""Minimal read-only MCP client scaffolding."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Callable

from archon.config import MCPConfig, MCPServerConfig


TransportFn = Callable[[MCPServerConfig, dict], object]


class MCPClient:
    def __init__(self, config: MCPConfig):
        self.config = config
        self._request_id = 0
        self._servers = {
            str(name).strip().lower(): server
            for name, server in getattr(config, "servers", {}).items()
            if str(name).strip()
        }

    def invoke(
        self,
        server_name: str,
        payload: dict,
        *,
        transport_fn: TransportFn | None = None,
    ) -> dict:
        resolved = self._resolve_server(server_name)
        if "error" in resolved:
            return resolved
        server_key = resolved["server"]
        server = resolved["server_config"]

        raw = self._dispatch(server, payload or {}, transport_fn=transport_fn)
        if isinstance(raw, dict) and raw.get("error"):
            return {"error": str(raw.get("error"))}
        content = self._cap_output(raw)
        return {
            "server": server_key,
            "mode": "read_only",
            "content": content,
            "truncated": str(raw or "") != content,
        }

    def list_tools(
        self,
        server_name: str,
        *,
        transport_fn: TransportFn | None = None,
    ) -> dict:
        resolved = self._resolve_server(server_name)
        if "error" in resolved:
            return resolved
        server_key = resolved["server"]
        server = resolved["server_config"]
        raw = self._dispatch(server, {"action": "tools/list"}, transport_fn=transport_fn)
        if isinstance(raw, dict) and raw.get("error"):
            return {"error": str(raw.get("error"))}
        if not isinstance(raw, dict):
            return {"error": f"Error: MCP server '{server_key}' returned an invalid tools/list response"}

        tools_raw = raw.get("tools", [])
        if not isinstance(tools_raw, list):
            return {"error": f"Error: MCP server '{server_key}' returned malformed tool metadata"}

        tools: list[dict] = []
        for item in tools_raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "") or "").strip()
            if not name:
                continue
            description = str(item.get("description", "") or "").strip()
            tools.append(
                {
                    "name": name,
                    "description": self._cap_output(description) if description else "",
                }
            )

        return {
            "server": server_key,
            "mode": "read_only",
            "tools": tools,
        }

    def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict | None = None,
        *,
        transport_fn: TransportFn | None = None,
    ) -> dict:
        resolved = self._resolve_server(server_name)
        if "error" in resolved:
            return resolved
        server_key = resolved["server"]
        server = resolved["server_config"]
        raw = self._dispatch(
            server,
            {
                "action": "tools/call",
                "name": str(tool_name or "").strip(),
                "arguments": arguments or {},
            },
            transport_fn=transport_fn,
        )
        if isinstance(raw, dict) and raw.get("error"):
            return {"error": str(raw.get("error"))}
        if not isinstance(raw, dict):
            return {"error": f"Error: MCP server '{server_key}' returned an invalid tools/call response"}

        content = self._format_tool_content(raw.get("content"))
        capped = self._cap_output(content)
        return {
            "server": server_key,
            "mode": "read_only",
            "tool": str(tool_name or "").strip(),
            "content": capped,
            "truncated": content != capped,
            "is_error": bool(raw.get("isError", False)),
        }

    def _cap_output(self, raw: object) -> str:
        text = str(raw or "")
        limit = max(1, int(getattr(self.config, "result_max_chars", 2000)))
        if len(text) <= limit:
            return text
        return text[: max(1, limit - 3)].rstrip() + "..."

    def _resolve_server(self, server_name: str) -> dict:
        server_key = str(server_name or "").strip().lower()
        server = self._servers.get(server_key)
        if server is None or not server.enabled:
            return {"error": f"Error: MCP server '{server_key}' is not available"}
        if str(server.mode or "").strip().lower() != "read_only":
            return {"error": f"Error: MCP server '{server_key}' must be read_only"}
        return {"server": server_key, "server_config": server}

    def _dispatch(
        self,
        server: MCPServerConfig,
        payload: dict,
        *,
        transport_fn: TransportFn | None,
    ) -> object:
        transport = transport_fn or self._default_transport
        try:
            return transport(server, payload)
        except Exception as e:
            return {"error": f"Error: MCP transport failed ({type(e).__name__}: {e})"}

    def _default_transport(self, server: MCPServerConfig, payload: dict) -> object:
        transport = str(server.transport or "").strip().lower()
        if transport != "stdio":
            return {"error": f"Error: MCP transport '{transport or 'unknown'}' is not supported"}
        command = [str(item).strip() for item in getattr(server, "command", []) if str(item).strip()]
        if not command:
            return {"error": "Error: MCP stdio server command is not configured"}
        action = str(payload.get("action", "") or "").strip().lower()
        if action not in {"tools/list", "tools/call"}:
            return {"error": f"Error: MCP action '{action or 'unknown'}' is not supported"}

        try:
            child_env = os.environ.copy()
            child_env.update(
                {
                    str(key): str(value)
                    for key, value in getattr(server, "env", {}).items()
                    if str(key).strip()
                }
            )
            proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=child_env,
            )
        except Exception as e:
            return {"error": f"Error: Failed to start MCP server ({type(e).__name__}: {e})"}

        try:
            init_result = self._send_jsonrpc(
                proc,
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "archon",
                        "version": "0.1.0",
                    },
                },
            )
            if isinstance(init_result, dict) and init_result.get("error"):
                return init_result

            if action == "tools/list":
                return self._send_jsonrpc(proc, "tools/list", {})

            return self._send_jsonrpc(
                proc,
                "tools/call",
                {
                    "name": str(payload.get("name", "") or "").strip(),
                    "arguments": payload.get("arguments", {}) or {},
                },
            )
        finally:
            self._close_process(proc)

    def _format_tool_content(self, raw_content: object) -> str:
        if isinstance(raw_content, str):
            return raw_content
        if not isinstance(raw_content, list):
            return str(raw_content or "")

        parts: list[str] = []
        for block in raw_content:
            if isinstance(block, dict) and str(block.get("type", "")).strip().lower() == "text":
                text = str(block.get("text", "") or "").strip()
                if text:
                    parts.append(text)
                continue
            if isinstance(block, dict):
                parts.append(json.dumps(block, sort_keys=True))
                continue
            if block is not None:
                parts.append(str(block))
        return "\n".join(part for part in parts if part)

    def _send_jsonrpc(self, proc: subprocess.Popen, method: str, params: dict) -> object:
        self._request_id += 1
        request_id = self._request_id
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        if proc.stdin is None or proc.stdout is None:
            return {"error": f"Error: MCP transport pipes are unavailable for method '{method}'"}
        proc.stdin.write(json.dumps(request) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        if not line:
            stderr_text = ""
            if proc.stderr is not None and proc.poll() is not None:
                try:
                    stderr_text = proc.stderr.read().strip()
                except Exception:
                    stderr_text = ""
            detail = f" | stderr: {stderr_text}" if stderr_text else ""
            return {"error": f"Error: MCP server closed without responding to '{method}'{detail}"}
        try:
            response = json.loads(line)
        except json.JSONDecodeError as e:
            return {"error": f"Error: MCP server returned invalid JSON for '{method}' ({e})"}
        if response.get("id") != request_id:
            return {"error": f"Error: MCP server returned mismatched response id for '{method}'"}
        if "error" in response:
            error = response.get("error") or {}
            message = str(getattr(error, "get", lambda *_a, **_k: "")("message", "") or error)
            return {"error": f"Error: MCP '{method}' failed ({message})"}
        return response.get("result", {})

    def _close_process(self, proc: subprocess.Popen) -> None:
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except Exception:
            pass
        try:
            if proc.stdout is not None:
                proc.stdout.close()
        except Exception:
            pass
        try:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=1)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        finally:
            try:
                if proc.stderr is not None:
                    proc.stderr.close()
            except Exception:
                pass
