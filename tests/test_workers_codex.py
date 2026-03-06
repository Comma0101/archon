"""Tests for the Codex CLI worker adapter."""

import subprocess
from pathlib import Path

from archon.workers.base import WorkerTask
from archon.workers.codex_cli import run_codex_task


class TestCodexCliAdapter:
    def test_returns_unavailable_when_binary_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr("archon.workers.codex_cli.shutil.which", lambda name: None)

        result = run_codex_task(
            WorkerTask(task="Review this repo", mode="review", repo_path=str(tmp_path))
        )

        assert result.status == "unavailable"
        assert result.worker == "codex"
        assert "not found" in result.summary.lower()

    def test_parses_jsonl_and_final_message(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "archon.workers.codex_cli.shutil.which",
            lambda name: "/usr/bin/codex",
        )

        def fake_run(command, cwd, capture_output, text, timeout):
            assert cwd == str(tmp_path.resolve())
            assert capture_output is True
            assert text is True
            assert timeout == 42
            assert command[0] == "/usr/bin/codex"
            assert command[1] == "exec"
            assert "--json" in command
            assert "--full-auto" in command
            assert "--sandbox" in command
            output_idx = command.index("--output-last-message") + 1
            Path(command[output_idx]).write_text("Final delegated answer")
            return subprocess.CompletedProcess(
                command,
                0,
                stdout='{"type":"session.started"}\n{"type":"response.completed"}\n',
                stderr="",
            )

        monkeypatch.setattr("archon.workers.codex_cli.subprocess.run", fake_run)

        result = run_codex_task(
            WorkerTask(
                task="Analyze failing tests",
                mode="analyze",
                repo_path=str(tmp_path),
                timeout_sec=42,
                constraints="Read-only.",
            )
        )

        assert result.status == "ok"
        assert result.exit_code == 0
        assert result.final_message == "Final delegated answer"
        assert len(result.events) == 2
        assert result.events[0].kind == "session.started"
        assert result.command[result.command.index("--sandbox") + 1] == "read-only"
        assert "Read-only." in result.command[-1]
