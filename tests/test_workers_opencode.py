"""Tests for the OpenCode CLI worker adapter."""

import subprocess

from archon.workers.base import WorkerTask
from archon.workers.opencode_cli import run_opencode_task


class TestOpenCodeCliAdapter:
    def test_returns_unavailable_when_binary_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr("archon.workers.opencode_cli.shutil.which", lambda name: None)

        result = run_opencode_task(
            WorkerTask(task="Review this repo", mode="review", repo_path=str(tmp_path))
        )

        assert result.status == "unavailable"
        assert result.worker == "opencode"
        assert "not found" in result.summary.lower()

    def test_parses_json_events_and_final_message(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "archon.workers.opencode_cli.shutil.which",
            lambda name: "/usr/bin/opencode",
        )

        def fake_run(command, cwd, capture_output, text, timeout):
            assert cwd == str(tmp_path.resolve())
            assert capture_output is True
            assert text is True
            assert timeout == 33
            assert command[0] == "/usr/bin/opencode"
            assert command[1] == "run"
            assert "--format" in command
            assert command[command.index("--format") + 1] == "json"
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    '{"type":"session.started","sessionId":"oc-123"}\n'
                    '{"type":"assistant","message":{"content":[{"text":"Folder overview complete"}]}}\n'
                    '{"type":"result","result":"Key modules identified."}\n'
                ),
                stderr="",
            )

        monkeypatch.setattr("archon.workers.opencode_cli.subprocess.run", fake_run)

        result = run_opencode_task(
            WorkerTask(
                task="Understand this folder",
                mode="analyze",
                repo_path=str(tmp_path),
                timeout_sec=33,
                constraints="Read-only analysis.",
            )
        )

        assert result.status == "ok"
        assert result.exit_code == 0
        assert result.vendor_session_id == "oc-123"
        assert len(result.events) == 3
        assert result.events[0].kind == "session.started"
        assert "Folder overview complete" in result.final_message or "Key modules identified." in result.final_message
        assert "Read-only analysis." in result.command[-1]

    def test_parses_realish_sessionID_and_text_event_shape(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "archon.workers.opencode_cli.shutil.which",
            lambda name: "/usr/bin/opencode",
        )

        def fake_run(command, cwd, capture_output, text, timeout):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    '{"type":"step_start","sessionID":"ses_real_123","part":{"id":"p1","sessionID":"ses_real_123"}}\n'
                    '{"type":"text","sessionID":"ses_real_123","part":{"text":"Final analysis from opencode"}}\n'
                    '{"type":"step_finish","sessionID":"ses_real_123","part":{"reason":"stop"}}\n'
                ),
                stderr="",
            )

        monkeypatch.setattr("archon.workers.opencode_cli.subprocess.run", fake_run)
        result = run_opencode_task(
            WorkerTask(task="Analyze", worker="opencode", mode="review", repo_path=str(tmp_path))
        )

        assert result.status == "ok"
        assert result.vendor_session_id == "ses_real_123"
        assert "Final analysis from opencode" in result.final_message

    def test_resume_session_id_is_forwarded(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "archon.workers.opencode_cli.shutil.which",
            lambda name: "/usr/bin/opencode",
        )

        def fake_run(command, cwd, capture_output, text, timeout):
            assert "--session" in command
            assert command[command.index("--session") + 1] == "oc-sess-9"
            return subprocess.CompletedProcess(
                command,
                0,
                stdout='{"type":"result","result":"done"}\n',
                stderr="",
            )

        monkeypatch.setattr("archon.workers.opencode_cli.subprocess.run", fake_run)
        result = run_opencode_task(
            WorkerTask(
                task="Follow-up",
                worker="opencode",
                mode="review",
                repo_path=str(tmp_path),
                resume_vendor_session_id="oc-sess-9",
            )
        )
        assert result.status == "ok"
