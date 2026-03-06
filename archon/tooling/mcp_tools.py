"""MCP tool registrations."""

from __future__ import annotations

from archon.mcp import MCPClient


def register_mcp_tools(registry) -> None:
    def mcp_call(server: str, tool: str, arguments: dict | None = None) -> str:
        client_cls = getattr(registry, "mcp_client_cls", None) or MCPClient
        config = getattr(registry, "config", None)
        if config is None:
            return "Error: MCP config is not available"

        result = client_cls(config.mcp).call_tool(
            server_name=server,
            tool_name=tool,
            arguments=arguments or {},
        )
        if result.get("error"):
            return str(result["error"])

        lines = [
            f"mcp_server: {result.get('server', '')}",
            f"mcp_tool: {result.get('tool', '')}",
            f"mcp_mode: {result.get('mode', '')}",
        ]
        if result.get("truncated"):
            lines.append("mcp_truncated: true")
        if result.get("is_error"):
            lines.append("mcp_result_error: true")
        content = str(result.get("content", "") or "")
        if content:
            lines.extend(["", content])
        return "\n".join(lines)

    registry.register(
        "mcp_call",
        "Call a read-only MCP tool exposed by a configured MCP server. Use only when the active profile allows MCP access.",
        {
            "properties": {
                "server": {
                    "type": "string",
                    "description": "Configured MCP server name",
                },
                "tool": {
                    "type": "string",
                    "description": "Advertised MCP tool name to call",
                },
                "arguments": {
                    "type": "object",
                    "description": "Arguments object for the MCP tool call",
                    "default": {},
                },
            },
            "required": ["server", "tool"],
        },
        mcp_call,
    )
