"""Voice service lifecycle tool registrations (Phase 0)."""

from __future__ import annotations

import subprocess

from archon.calls import runner as voice_runner
from archon.config import load_config
from archon.safety import Level


def _format_output(lines: list[str]) -> str:
    return "\n".join([line for line in lines if line])


def _run_systemd_unit(action: str, unit: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", "--user", action, unit],
        capture_output=True,
        text=True,
        timeout=30,
    )


def _voice_service_cfg():
    cfg = load_config()
    return cfg, cfg.calls, cfg.calls.voice_service


def register_call_service_tools(registry) -> None:
    def voice_service_status() -> str:
        payload = voice_runner.voice_service_health()
        ok = bool(payload.get("ok"))
        status = str(payload.get("status") or ("healthy" if ok else "unknown"))
        running = "yes" if ok else "no"
        lines = [
            f"status: {status}",
            f"ok: {ok}",
            f"running: {running}",
        ]
        base_url = payload.get("base_url")
        if base_url:
            lines.append(f"base_url: {base_url}")
        reason = payload.get("reason")
        if reason:
            lines.append(f"reason: {reason}")
        return _format_output(lines)

    registry.register(
        "voice_service_status",
        "Check whether the local Archon voice service is reachable and healthy.",
        {
            "properties": {},
            "required": [],
        },
        voice_service_status,
    )

    def _voice_service_action(action: str) -> str:
        try:
            _cfg, calls_cfg, svc_cfg = _voice_service_cfg()
        except Exception as e:
            return f"Error: failed to load config ({type(e).__name__}: {e})"

        if not calls_cfg.enabled:
            return _format_output(
                [
                    f"action: {action}",
                    "status: disabled",
                    "reason: calls.disabled",
                ]
            )

        mode = str(svc_cfg.mode or "").strip().lower() or "systemd"
        if mode == "systemd":
            unit = str(svc_cfg.systemd_unit or "archon-voice.service").strip()
            if not unit:
                return _format_output(
                    [
                        f"action: {action}",
                        "mode: systemd",
                        "status: error",
                        "reason: calls.voice_service.systemd_unit missing",
                    ]
                )
            if not registry.confirmer(f"Voice service {action} ({mode})", Level.DANGEROUS):
                return "Voice service action rejected by safety gate."
            try:
                result = _run_systemd_unit(action, unit)
            except Exception as e:
                return _format_output(
                    [
                        f"action: {action}",
                        "mode: systemd",
                        f"unit: {unit}",
                        "status: error",
                        f"reason: {type(e).__name__}: {e}",
                    ]
                )

            stdout = (result.stdout or "").strip()
            stderr = (result.stderr or "").strip()
            lines = [
                f"action: {action}",
                "mode: systemd",
                f"unit: {unit}",
                "status: ok" if result.returncode == 0 else "status: error",
                f"return_code: {result.returncode}",
            ]
            if stdout:
                lines.append(f"stdout: {stdout}")
            if stderr:
                lines.append(f"stderr: {stderr}")
            return _format_output(lines)

        if mode == "subprocess":
            return _format_output(
                [
                    f"action: {action}",
                    "mode: subprocess",
                    "status: unsupported",
                    "reason: subprocess lifecycle mode is not implemented yet",
                ]
            )

        return _format_output(
            [
                f"action: {action}",
                f"mode: {mode or 'unknown'}",
                "status: error",
                "reason: unsupported calls.voice_service.mode",
            ]
        )

    def voice_service_start() -> str:
        return _voice_service_action("start")

    registry.register(
        "voice_service_start",
        "Start the local Archon voice service via the configured user systemd unit.",
        {
            "properties": {},
            "required": [],
        },
        voice_service_start,
    )

    def voice_service_stop() -> str:
        return _voice_service_action("stop")

    registry.register(
        "voice_service_stop",
        "Stop the local Archon voice service via the configured user systemd unit.",
        {
            "properties": {},
            "required": [],
        },
        voice_service_stop,
    )
