"""Tests for voice service lifecycle tools."""

import subprocess

from archon.config import Config
from archon.safety import Level
from archon.tools import ToolRegistry


def make_registry(confirmer=None):
    return ToolRegistry(archon_source_dir=None, confirmer=confirmer)


def _calls_config(*, enabled: bool = True, mode: str = "systemd", unit: str = "archon-voice.service") -> Config:
    cfg = Config()
    cfg.calls.enabled = enabled
    cfg.calls.voice_service.mode = mode
    cfg.calls.voice_service.systemd_unit = unit
    return cfg


class TestCallServiceTools:
    def test_voice_service_schemas_describe_systemd_control_only(self):
        registry = make_registry()
        schemas = {schema["name"]: schema for schema in registry.get_schemas()}

        assert schemas["voice_service_start"]["description"] == (
            "Start the local Archon voice service via the configured user systemd unit."
        )
        assert schemas["voice_service_stop"]["description"] == (
            "Stop the local Archon voice service via the configured user systemd unit."
        )

    def test_voice_service_status_reports_health(self, monkeypatch):
        monkeypatch.setattr(
            "archon.tooling.call_service_tools.voice_runner.voice_service_health",
            lambda config=None: {
                "ok": True,
                "status": "healthy",
                "base_url": "http://127.0.0.1:8788",
            },
        )

        registry = make_registry()
        result = registry.execute("voice_service_status", {})

        assert "status: healthy" in result
        assert "base_url: http://127.0.0.1:8788" in result

    def test_voice_service_start_requires_confirmation(self, monkeypatch):
        calls = []
        commands = []

        def fake_confirmer(command, level):
            calls.append((command, level))
            return True

        def fake_run(command, capture_output, text, timeout):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        monkeypatch.setattr(
            "archon.tooling.call_service_tools.load_config",
            lambda: _calls_config(enabled=True, mode="systemd", unit="archon-voice.service"),
        )
        monkeypatch.setattr("archon.tooling.call_service_tools.subprocess.run", fake_run)

        registry = make_registry(confirmer=fake_confirmer)
        out = registry.execute("voice_service_start", {})

        assert "mode: systemd" in out
        assert "action: start" in out
        assert calls
        assert calls[0][1] == Level.DANGEROUS
        assert commands[0] == ["systemctl", "--user", "start", "archon-voice.service"]

    def test_voice_service_stop_requires_confirmation(self, monkeypatch):
        calls = []
        commands = []

        def fake_confirmer(command, level):
            calls.append((command, level))
            return True

        def fake_run(command, capture_output, text, timeout):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        monkeypatch.setattr(
            "archon.tooling.call_service_tools.load_config",
            lambda: _calls_config(enabled=True, mode="systemd", unit="archon-voice.service"),
        )
        monkeypatch.setattr("archon.tooling.call_service_tools.subprocess.run", fake_run)

        registry = make_registry(confirmer=fake_confirmer)
        out = registry.execute("voice_service_stop", {})

        assert "mode: systemd" in out
        assert "action: stop" in out
        assert calls
        assert calls[0][1] == Level.DANGEROUS
        assert commands[0] == ["systemctl", "--user", "stop", "archon-voice.service"]

    def test_voice_service_subprocess_mode_reports_unsupported_without_confirmation(self, monkeypatch):
        calls = []

        def fake_confirmer(command, level):
            calls.append((command, level))
            return True

        monkeypatch.setattr(
            "archon.tooling.call_service_tools.load_config",
            lambda: _calls_config(enabled=True, mode="subprocess", unit="archon-voice.service"),
        )

        registry = make_registry(confirmer=fake_confirmer)
        out = registry.execute("voice_service_start", {})

        assert "mode: subprocess" in out
        assert "status: unsupported" in out
        assert "reason: subprocess lifecycle mode is not implemented yet" in out
        assert calls == []
