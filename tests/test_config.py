"""Tests for configuration loading."""

from archon.config import ProfileConfig, load_config
from archon.control.skills import BUILTIN_SKILLS, DEFAULT_SKILL_NAME, resolve_skill_profile


def test_load_config_calls_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr("archon.config.CONFIG_DIR", tmp_path / "config" / "archon")

    cfg = load_config()

    assert cfg.calls.enabled is False
    assert cfg.calls.realtime.enabled is False
    assert cfg.calls.realtime.provider in {"deepgram_voice_agent_v1"}
    assert cfg.calls.voice_service.mode in {"systemd", "subprocess"}
    assert cfg.agent.llm_request_timeout_sec == 45.0
    assert cfg.agent.llm_retry_attempts == 3
    assert cfg.agent.tool_result_max_chars == 6000
    assert cfg.agent.tool_result_worker_max_chars == 2500
    assert cfg.orchestrator.enabled is False
    assert cfg.orchestrator.mode == "legacy"
    assert cfg.orchestrator.shadow_eval is True
    assert cfg.orchestrator.default_profile == "default"
    assert cfg.research.google_deep_research.enabled is False
    assert cfg.research.google_deep_research.agent == "deep-research-pro-preview-12-2025"
    assert cfg.research.google_deep_research.timeout_minutes == 20
    assert cfg.research.google_deep_research.poll_interval_sec == 10


def test_load_config_orchestrator_section(monkeypatch, tmp_path):
    config_dir = tmp_path / "config" / "archon"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.toml").write_text(
        "\n".join(
            [
                "[orchestrator]",
                "enabled = true",
                'mode = "hybrid"',
                "shadow_eval = false",
                'default_profile = "safe"',
            ]
        )
    )
    monkeypatch.setattr("archon.config.CONFIG_DIR", config_dir)

    cfg = load_config()

    assert cfg.orchestrator.enabled is True
    assert cfg.orchestrator.mode == "hybrid"
    assert cfg.orchestrator.shadow_eval is False
    assert cfg.orchestrator.default_profile == "safe"


def test_load_config_profiles_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr("archon.config.CONFIG_DIR", tmp_path / "config" / "archon")
    cfg = load_config()
    assert "default" in cfg.profiles
    assert cfg.profiles["default"].allowed_tools == ["*"]
    assert cfg.profiles["default"].max_mode == "implement"
    assert cfg.profiles["default"].execution_backend == "host"
    assert cfg.profiles["default"].skill == ""
    assert cfg.profiles["default"].allowed_tools_explicit is False
    assert cfg.profiles["default"].max_mode_explicit is False


def test_load_config_profiles_section(monkeypatch, tmp_path):
    config_dir = tmp_path / "config" / "archon"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.toml").write_text(
        "\n".join(
            [
                "[profiles.default]",
                'allowed_tools = ["read_file", "list_dir"]',
                'max_mode = "review"',
                'execution_backend = "host"',
                'skill = "general"',
                "",
                "[profiles.safe]",
                'allowed_tools = ["memory_read"]',
                'max_mode = "analyze"',
                'execution_backend = "subprocess-restricted"',
                'skill = "researcher"',
            ]
        )
    )
    monkeypatch.setattr("archon.config.CONFIG_DIR", config_dir)

    cfg = load_config()

    assert set(cfg.profiles.keys()) == {"default", "safe"}
    assert cfg.profiles["default"].allowed_tools == ["read_file", "list_dir"]
    assert cfg.profiles["default"].max_mode == "review"
    assert cfg.profiles["default"].skill == "general"
    assert cfg.profiles["default"].allowed_tools_explicit is True
    assert cfg.profiles["default"].max_mode_explicit is True
    assert cfg.profiles["safe"].allowed_tools == ["memory_read"]
    assert cfg.profiles["safe"].max_mode == "analyze"
    assert cfg.profiles["safe"].execution_backend == "subprocess-restricted"
    assert cfg.profiles["safe"].skill == "researcher"
    assert cfg.profiles["safe"].allowed_tools_explicit is True
    assert cfg.profiles["safe"].max_mode_explicit is True


def test_load_config_agent_tool_result_caps(monkeypatch, tmp_path):
    config_dir = tmp_path / "config" / "archon"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.toml").write_text(
        "\n".join(
            [
                "[agent]",
                "tool_result_max_chars = 7000",
                "tool_result_worker_max_chars = 1800",
            ]
        )
    )
    monkeypatch.setattr("archon.config.CONFIG_DIR", config_dir)

    cfg = load_config()

    assert cfg.agent.tool_result_max_chars == 7000
    assert cfg.agent.tool_result_worker_max_chars == 1800


def test_load_config_mcp_read_only_servers(monkeypatch, tmp_path):
    config_dir = tmp_path / "config" / "archon"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.toml").write_text(
        "\n".join(
            [
                "[mcp]",
                "result_max_chars = 1800",
                "",
                "[mcp.servers.docs]",
                "enabled = true",
                'mode = "read_only"',
                'transport = "stdio"',
                'command = ["python", "server.py"]',
            ]
        )
    )
    monkeypatch.setattr("archon.config.CONFIG_DIR", config_dir)

    cfg = load_config()

    assert cfg.mcp.result_max_chars == 1800
    assert "docs" in cfg.mcp.servers
    server = cfg.mcp.servers["docs"]
    assert server.enabled is True
    assert server.mode == "read_only"
    assert server.transport == "stdio"
    assert server.command == ["python", "server.py"]


def test_load_config_mcp_server_env_interpolates_from_environment(monkeypatch, tmp_path):
    config_dir = tmp_path / "config" / "archon"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.toml").write_text(
        "\n".join(
            [
                "[mcp.servers.exa]",
                "enabled = true",
                'mode = "read_only"',
                'transport = "stdio"',
                'command = ["node", "server.js"]',
                "",
                "[mcp.servers.exa.env]",
                'EXA_API_KEY = "${EXA_API_KEY}"',
                'LOG_LEVEL = "debug"',
            ]
        )
    )
    monkeypatch.setattr("archon.config.CONFIG_DIR", config_dir)
    monkeypatch.setenv("EXA_API_KEY", "test-exa-secret")

    cfg = load_config()

    assert "exa" in cfg.mcp.servers
    server = cfg.mcp.servers["exa"]
    assert server.env == {
        "EXA_API_KEY": "test-exa-secret",
        "LOG_LEVEL": "debug",
    }


def test_load_config_mcp_server_names_are_normalized_lowercase(monkeypatch, tmp_path):
    config_dir = tmp_path / "config" / "archon"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.toml").write_text(
        "\n".join(
            [
                "[mcp.servers.Docs]",
                "enabled = true",
                'mode = "read_only"',
                'transport = "stdio"',
                'command = ["python", "server.py"]',
            ]
        )
    )
    monkeypatch.setattr("archon.config.CONFIG_DIR", config_dir)

    cfg = load_config()

    assert "docs" in cfg.mcp.servers
    assert "Docs" not in cfg.mcp.servers


def test_load_config_parses_google_deep_research_settings(monkeypatch, tmp_path):
    config_dir = tmp_path / "config" / "archon"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.toml").write_text(
        "\n".join(
            [
                "[research.google_deep_research]",
                "enabled = true",
                'agent = "deep-research-pro-preview-12-2025"',
                "timeout_minutes = 25",
                "poll_interval_sec = 7",
                'thinking_summaries = "auto"',
            ]
        )
    )
    monkeypatch.setattr("archon.config.CONFIG_DIR", config_dir)

    cfg = load_config()

    assert cfg.research.google_deep_research.enabled is True
    assert cfg.research.google_deep_research.agent == "deep-research-pro-preview-12-2025"
    assert cfg.research.google_deep_research.timeout_minutes == 25
    assert cfg.research.google_deep_research.poll_interval_sec == 7
    assert cfg.research.google_deep_research.thinking_summaries == "auto"


def test_builtin_skill_registry_contains_expected_skills():
    assert DEFAULT_SKILL_NAME == "general"
    assert set(BUILTIN_SKILLS.keys()) == {
        "general",
        "coder",
        "researcher",
        "operator",
        "sales",
        "memory_curator",
    }


def test_resolve_skill_profile_uses_skill_defaults_for_generic_profile():
    resolved = resolve_skill_profile(ProfileConfig(skill="researcher"))

    assert resolved.skill_name == "researcher"
    assert "web_search" in resolved.allowed_tools
    assert "web_read" in resolved.allowed_tools
    assert "shell" not in resolved.allowed_tools
    assert resolved.preferred_provider == "openai"
    assert resolved.preferred_model == "gpt-4o"


def test_resolve_skill_profile_keeps_explicit_profile_overrides():
    resolved = resolve_skill_profile(
        ProfileConfig(
            skill="coder",
            allowed_tools=["read_file"],
            max_mode="review",
            execution_backend="subprocess-restricted",
        )
    )

    assert resolved.skill_name == "coder"
    assert resolved.allowed_tools == ("read_file",)
    assert resolved.max_mode == "review"
    assert resolved.execution_backend == "subprocess-restricted"
    assert resolved.preferred_provider == "anthropic"
    assert resolved.preferred_model == "claude-sonnet-4-6"


def test_resolve_skill_profile_unknown_skill_fails_closed():
    resolved = resolve_skill_profile(ProfileConfig(skill="not_real"))

    assert resolved.skill_name == ""
    assert resolved.allowed_tools == ()
    assert resolved.preferred_provider == ""
    assert resolved.preferred_model == ""
    assert resolved.prompt_guidance == ""


def test_load_config_skill_profile_explicit_wildcard_override_stays_explicit(
    monkeypatch, tmp_path
):
    config_dir = tmp_path / "config" / "archon"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.toml").write_text(
        "\n".join(
            [
                "[profiles.research]",
                'skill = "researcher"',
                'allowed_tools = ["*"]',
            ]
        )
    )
    monkeypatch.setattr("archon.config.CONFIG_DIR", config_dir)

    cfg = load_config()
    resolved = resolve_skill_profile(cfg.profiles["research"])

    assert cfg.profiles["research"].allowed_tools_explicit is True
    assert resolved.skill_name == "researcher"
    assert resolved.allowed_tools == ("*",)
