import sys

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


def test_mcp_client_lists_tools_from_transport():
    client = MCPClient(
        MCPConfig(
            servers={
                "docs": MCPServerConfig(
                    enabled=True,
                    mode="read_only",
                    transport="stdio",
                    command=["python", "server.py"],
                )
            }
        )
    )

    result = client.list_tools(
        "docs",
        transport_fn=lambda _server, payload: {
            "tools": [
                {
                    "name": "search_docs",
                    "description": f"for {payload['action']}",
                }
            ]
        },
    )

    assert result["server"] == "docs"
    assert len(result["tools"]) == 1
    assert result["tools"][0]["name"] == "search_docs"
    assert "tools/list" in result["tools"][0]["description"]


def test_mcp_client_stdio_transport_initializes_before_listing_tools(tmp_path):
    log_path = tmp_path / "mcp-log.txt"
    server_path = tmp_path / "fake_mcp_server.py"
    server_path.write_text(
        "\n".join(
            [
                "import json",
                "import pathlib",
                "import sys",
                "",
                "log_path = pathlib.Path(sys.argv[1])",
                "for line in sys.stdin:",
                "    if not line.strip():",
                "        continue",
                "    request = json.loads(line)",
                "    method = request.get('method', '')",
                "    with log_path.open('a', encoding='utf-8') as handle:",
                "        handle.write(method + '\\n')",
                "    response = {'jsonrpc': '2.0', 'id': request.get('id')}",
                "    if method == 'initialize':",
                "        response['result'] = {",
                "            'protocolVersion': '2025-06-18',",
                "            'capabilities': {'tools': {}},",
                "            'serverInfo': {'name': 'fake-docs', 'version': '0.1.0'},",
                "        }",
                "    elif method == 'tools/list':",
                "        response['result'] = {",
                "            'tools': [{'name': 'search_docs', 'description': 'Search docs'}],",
                "        }",
                "    else:",
                "        response['error'] = {'code': -32601, 'message': method}",
                "    sys.stdout.write(json.dumps(response) + '\\n')",
                "    sys.stdout.flush()",
            ]
        ),
        encoding="utf-8",
    )

    client = MCPClient(
        MCPConfig(
            servers={
                "docs": MCPServerConfig(
                    enabled=True,
                    mode="read_only",
                    transport="stdio",
                    command=[sys.executable, str(server_path), str(log_path)],
                )
            }
        )
    )

    result = client.list_tools("docs")

    assert result["server"] == "docs"
    assert result["tools"][0]["name"] == "search_docs"
    assert log_path.read_text(encoding="utf-8").splitlines() == ["initialize", "tools/list"]


def test_mcp_client_stdio_transport_passes_server_env_to_child(tmp_path):
    log_path = tmp_path / "mcp-env-log.txt"
    server_path = tmp_path / "fake_env_mcp_server.py"
    server_path.write_text(
        "\n".join(
            [
                "import json",
                "import os",
                "import pathlib",
                "import sys",
                "",
                "log_path = pathlib.Path(sys.argv[1])",
                "for line in sys.stdin:",
                "    if not line.strip():",
                "        continue",
                "    request = json.loads(line)",
                "    method = request.get('method', '')",
                "    response = {'jsonrpc': '2.0', 'id': request.get('id')}",
                "    if method == 'initialize':",
                "        with log_path.open('w', encoding='utf-8') as handle:",
                "            handle.write(os.environ.get('EXA_API_KEY', ''))",
                "        response['result'] = {",
                "            'protocolVersion': '2025-06-18',",
                "            'capabilities': {'tools': {}},",
                "            'serverInfo': {'name': 'fake-exa', 'version': '0.1.0'},",
                "        }",
                "    elif method == 'tools/list':",
                "        response['result'] = {",
                "            'tools': [{'name': 'web_search', 'description': 'Search Exa'}],",
                "        }",
                "    else:",
                "        response['error'] = {'code': -32601, 'message': method}",
                "    sys.stdout.write(json.dumps(response) + '\\n')",
                "    sys.stdout.flush()",
            ]
        ),
        encoding="utf-8",
    )

    client = MCPClient(
        MCPConfig(
            servers={
                "exa": MCPServerConfig(
                    enabled=True,
                    mode="read_only",
                    transport="stdio",
                    command=[sys.executable, str(server_path), str(log_path)],
                    env={"EXA_API_KEY": "test-exa-secret"},
                )
            }
        )
    )

    result = client.list_tools("exa")

    assert result["server"] == "exa"
    assert result["tools"][0]["name"] == "web_search"
    assert log_path.read_text(encoding="utf-8") == "test-exa-secret"
