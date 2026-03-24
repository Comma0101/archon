"""Tests for native subagent tier config resolution."""

from archon.config import Config, TierConfig, load_config, resolve_tier_model


def test_config_has_default_tiers():
    cfg = Config()

    assert cfg.tiers == TierConfig()


def test_load_config_parses_llm_tiers_section(monkeypatch, tmp_path):
    config_dir = tmp_path / "config" / "archon"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.toml").write_text(
        "\n".join(
            [
                "[llm]",
                'provider = "anthropic"',
                'model = "claude-sonnet-4-6"',
                "",
                "[llm.tiers]",
                'light = "claude-haiku-4-5-20251001"',
                'standard = "claude-sonnet-4-6"',
            ]
        )
    )
    monkeypatch.setattr("archon.config.CONFIG_DIR", config_dir)

    cfg = load_config()

    assert cfg.tiers.light == "claude-haiku-4-5-20251001"
    assert cfg.tiers.standard == "claude-sonnet-4-6"


def test_resolve_tier_model_auto_detects_light_models():
    anthropic_cfg = Config()
    anthropic_cfg.llm.provider = "anthropic"

    openai_cfg = Config()
    openai_cfg.llm.provider = "openai"

    google_cfg = Config()
    google_cfg.llm.provider = "google"

    assert resolve_tier_model(anthropic_cfg, "light") == "claude-haiku-4-5-20251001"
    assert resolve_tier_model(openai_cfg, "light") == "gpt-4o-mini"
    assert resolve_tier_model(google_cfg, "light") == "gemini-2.5-flash"


def test_resolve_tier_model_standard_inherits_config_model():
    cfg = Config()
    cfg.llm.model = "gpt-5.2"

    assert resolve_tier_model(cfg, "standard") == "gpt-5.2"


def test_resolve_tier_model_explicit_tier_overrides_win():
    cfg = Config()
    cfg.llm.provider = "anthropic"
    cfg.llm.model = "claude-sonnet-4-6"
    cfg.tiers.light = "custom-light"
    cfg.tiers.standard = "custom-standard"

    assert resolve_tier_model(cfg, "light") == "custom-light"
    assert resolve_tier_model(cfg, "standard") == "custom-standard"
