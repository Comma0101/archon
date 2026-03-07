"""Tests that write_file and edit_file always require confirmation."""

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from archon.safety import Level


def _make_registry(confirm_result=True):
    registry = MagicMock()
    registry.archon_source_dir = None
    registry.confirmer = MagicMock(return_value=confirm_result)
    return registry


def test_write_file_confirms_in_home(tmp_path, monkeypatch):
    """write_file must confirm even for paths inside $HOME."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    registry = _make_registry(confirm_result=True)

    from archon.tooling.filesystem_tools import register_filesystem_tools
    register_filesystem_tools(registry)

    calls = registry.register.call_args_list
    write_call = [c for c in calls if c[0][0] == "write_file"][0]
    handler = write_call[0][3]  # 4th positional arg is the handler

    target = tmp_path / "test_output.py"
    result = handler(path=str(target), content="hello")

    registry.confirmer.assert_called_once()
    call_args = registry.confirmer.call_args
    assert "write" in call_args[0][0].lower() or "Write" in call_args[0][0]


def test_write_file_rejected_in_home(tmp_path, monkeypatch):
    """write_file must respect rejection for paths inside $HOME."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    registry = _make_registry(confirm_result=False)

    from archon.tooling.filesystem_tools import register_filesystem_tools
    register_filesystem_tools(registry)

    calls = registry.register.call_args_list
    write_call = [c for c in calls if c[0][0] == "write_file"][0]
    handler = write_call[0][3]

    target = tmp_path / "rejected.py"
    result = handler(path=str(target), content="hello")

    assert "rejected" in result.lower() or "safety" in result.lower()
    assert not target.exists()


def test_edit_file_confirms_in_home(tmp_path, monkeypatch):
    """edit_file must confirm even for paths inside $HOME."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    registry = _make_registry(confirm_result=True)

    from archon.tooling.filesystem_tools import register_filesystem_tools
    register_filesystem_tools(registry)

    calls = registry.register.call_args_list
    edit_call = [c for c in calls if c[0][0] == "edit_file"][0]
    handler = edit_call[0][3]

    target = tmp_path / "existing.py"
    target.write_text("old_content")

    result = handler(path=str(target), old="old_content", new="new_content")

    registry.confirmer.assert_called_once()


def test_edit_file_rejected_in_home(tmp_path, monkeypatch):
    """edit_file must respect rejection for paths inside $HOME."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    registry = _make_registry(confirm_result=False)

    from archon.tooling.filesystem_tools import register_filesystem_tools
    register_filesystem_tools(registry)

    calls = registry.register.call_args_list
    edit_call = [c for c in calls if c[0][0] == "edit_file"][0]
    handler = edit_call[0][3]

    target = tmp_path / "existing.py"
    target.write_text("old_content")

    result = handler(path=str(target), old="old_content", new="new_content")

    assert "rejected" in result.lower() or "safety" in result.lower()
    assert target.read_text() == "old_content"
