from archon.config import Config, MCPConfig, MCPServerConfig, ProfileConfig
from archon.control.policy import evaluate_mcp_policy
from archon.mcp.client import MCPClient


def test_mcp_policy_denies_server_when_profile_disallows_mcp():
    cfg = Config()
    cfg.orchestrator.enabled = True
    cfg.orchestrator.mode = "hybrid"
    cfg.orchestrator.shadow_eval = False
    cfg.profiles = {
        "default": ProfileConfig(),
        "safe": ProfileConfig(allowed_tools=["memory_read"]),
    }

    decision = evaluate_mcp_policy(
        config=cfg,
        server_name="docs",
        profile_name="safe",
    )

    assert decision.decision == "deny"
    assert decision.reason == "mcp_not_allowed"
    assert decision.profile == "safe"
    assert decision.tool_name == "mcp:docs"


def test_mcp_client_registers_read_only_server_and_caps_output():
    client = MCPClient(
        MCPConfig(
            result_max_chars=32,
            servers={
                "docs": MCPServerConfig(
                    enabled=True,
                    mode="read_only",
                    transport="stdio",
                    command=["python", "server.py"],
                )
            },
        )
    )

    result = client.invoke(
        "docs",
        {"prompt": "hello"},
        transport_fn=lambda _server, _payload: "A" * 80,
    )

    assert result["server"] == "docs"
    assert result["mode"] == "read_only"
    assert result["truncated"] is True
    assert len(result["content"]) <= 32


def test_mcp_client_rejects_non_read_only_servers():
    client = MCPClient(
        MCPConfig(
            servers={
                "writer": MCPServerConfig(
                    enabled=True,
                    mode="read_write",
                    transport="stdio",
                    command=["python", "server.py"],
                )
            },
        )
    )

    result = client.invoke(
        "writer",
        {"prompt": "hello"},
        transport_fn=lambda _server, _payload: "ok",
    )

    assert result["error"].startswith("Error:")
    assert "read_only" in result["error"]


def test_mcp_client_normalizes_server_name_lookup():
    client = MCPClient(
        MCPConfig(
            servers={
                "Docs": MCPServerConfig(
                    enabled=True,
                    mode="read_only",
                    transport="stdio",
                    command=["python", "server.py"],
                )
            }
        )
    )

    result = client.invoke(
        "docs",
        {"prompt": "hello"},
        transport_fn=lambda _server, _payload: "ok",
    )

    assert result["server"] == "docs"
    assert result["content"] == "ok"
