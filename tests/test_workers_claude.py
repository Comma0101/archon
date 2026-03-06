"""Tests for the Claude Code CLI worker adapter."""

import subprocess

from archon.workers.base import WorkerTask
from archon.workers.claude_code_cli import run_claude_code_task


class TestClaudeCodeCliAdapter:
    def test_returns_unavailable_when_binary_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr("archon.workers.claude_code_cli.shutil.which", lambda name: None)

        result = run_claude_code_task(
            WorkerTask(task="Review this repo", mode="review", repo_path=str(tmp_path))
        )

        assert result.status == "unavailable"
        assert result.worker == "claude_code"

    def test_review_mode_parses_stream_json(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "archon.workers.claude_code_cli.shutil.which",
            lambda name: "/usr/bin/claude",
        )

        def fake_run(command, cwd, capture_output, text, timeout, env=None):
            assert cwd == str(tmp_path.resolve())
            assert command[0] == "/usr/bin/claude"
            assert "-p" in command
            assert "--output-format" in command
            assert command[command.index("--output-format") + 1] == "stream-json"
            assert "--permission-mode" in command
            assert command[command.index("--permission-mode") + 1] == "dontAsk"
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    '{"type":"session.started","session_id":"abc123"}\n'
                    '{"type":"assistant","message":{"content":[{"text":"Review complete"}]}}\n'
                    '{"type":"result","result":"No issues found."}\n'
                ),
                stderr="",
            )

        monkeypatch.setattr("archon.workers.claude_code_cli.subprocess.run", fake_run)

        result = run_claude_code_task(
            WorkerTask(
                task="Review the code",
                mode="review",
                repo_path=str(tmp_path),
                timeout_sec=30,
            )
        )

        assert result.status == "ok"
        assert result.vendor_session_id == "abc123"
        assert len(result.events) == 3
        assert "Review complete" in result.final_message or "No issues found." in result.final_message

    def test_implement_mode_is_explicitly_unsupported_for_now(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "archon.workers.claude_code_cli.shutil.which",
            lambda name: "/usr/bin/claude",
        )
        result = run_claude_code_task(
            WorkerTask(task="Fix bug", mode="implement", repo_path=str(tmp_path))
        )
        assert result.status == "unsupported"
        assert "requires an Archon worker session ID" in result.summary

    def test_resume_session_id_is_forwarded(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "archon.workers.claude_code_cli.shutil.which",
            lambda name: "/usr/bin/claude",
        )

        def fake_run(command, cwd, capture_output, text, timeout, env=None):
            assert "--resume" in command
            assert command[command.index("--resume") + 1] == "vendor-abc"
            return subprocess.CompletedProcess(
                command,
                0,
                stdout='{"type":"result","result":"done"}\n',
                stderr="",
            )

        monkeypatch.setattr("archon.workers.claude_code_cli.subprocess.run", fake_run)
        result = run_claude_code_task(
            WorkerTask(
                task="Follow-up",
                worker="claude_code",
                mode="review",
                repo_path=str(tmp_path),
                resume_vendor_session_id="vendor-abc",
            )
        )
        assert result.status == "ok"

    def test_implement_mode_uses_permission_broker_when_supported(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "archon.workers.claude_code_cli.shutil.which",
            lambda name: "/usr/bin/claude",
        )
        monkeypatch.setattr(
            "archon.workers.claude_code_cli._supports_permission_prompt_tool",
            lambda _bin: True,
        )
        monkeypatch.setattr(
            "archon.workers.claude_code_cli._prepare_permission_broker",
            lambda task: {
                "env": {"X": "1"},
                "mcp_config_path": "/tmp/mcp.json",
                "permission_tool_name": "mcp__archon_approval__permission_prompt",
            },
        )
        monkeypatch.setattr("archon.workers.claude_code_cli._cleanup_broker", lambda ctx: None)

        def fake_run(command, cwd, capture_output, text, timeout, env=None):
            assert "--permission-mode" in command
            assert command[command.index("--permission-mode") + 1] == "default"
            assert "--mcp-config" in command
            assert command[command.index("--mcp-config") + 1] == "/tmp/mcp.json"
            assert "--permission-prompt-tool" in command
            assert env == {"X": "1"}
            return subprocess.CompletedProcess(
                command,
                0,
                stdout='{"type":"result","result":"done"}\n',
                stderr="",
            )

        monkeypatch.setattr("archon.workers.claude_code_cli.subprocess.run", fake_run)
        result = run_claude_code_task(
            WorkerTask(
                task="Fix bug",
                worker="claude_code",
                mode="implement",
                repo_path=str(tmp_path),
                archon_session_id="sess-1",
                timeout_sec=60,
            )
        )
        assert result.status == "ok"
