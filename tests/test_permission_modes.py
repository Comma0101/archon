"""Tests for permission modes."""

from archon.config import SafetyConfig


def test_default_permission_mode():
    cfg = SafetyConfig()
    assert cfg.permission_mode == "confirm_all"


def test_valid_permission_modes():
    for mode in ("confirm_all", "accept_reads", "auto"):
        cfg = SafetyConfig(permission_mode=mode)
        assert cfg.permission_mode == mode
