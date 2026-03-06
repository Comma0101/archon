"""Tests for configuration loading."""

from archon.config import load_config


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
                "",
                "[profiles.safe]",
                'allowed_tools = ["memory_read"]',
                'max_mode = "analyze"',
                'execution_backend = "subprocess-restricted"',
            ]
        )
    )
    monkeypatch.setattr("archon.config.CONFIG_DIR", config_dir)

    cfg = load_config()

    assert set(cfg.profiles.keys()) == {"default", "safe"}
    assert cfg.profiles["default"].allowed_tools == ["read_file", "list_dir"]
    assert cfg.profiles["default"].max_mode == "review"
    assert cfg.profiles["safe"].allowed_tools == ["memory_read"]
    assert cfg.profiles["safe"].max_mode == "analyze"
    assert cfg.profiles["safe"].execution_backend == "subprocess-restricted"


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
