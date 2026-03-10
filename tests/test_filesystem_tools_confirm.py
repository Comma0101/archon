from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

def _make_registry(confirm_result=True, *, permission_mode="confirm_all", archon_source_dir=None):
    registry = MagicMock()
    registry.archon_source_dir = archon_source_dir
    registry.confirmer = MagicMock(return_value=confirm_result)
    registry.config = SimpleNamespace(
        safety=SimpleNamespace(permission_mode=permission_mode),
    )
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


def test_write_file_treats_home_prefix_sibling_as_outside_home(tmp_path, monkeypatch):
    fake_home = tmp_path / "comma"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    registry = _make_registry(confirm_result=False)

    from archon.tooling.filesystem_tools import register_filesystem_tools
    register_filesystem_tools(registry)

    calls = registry.register.call_args_list
    write_call = [c for c in calls if c[0][0] == "write_file"][0]
    handler = write_call[0][3]

    target = tmp_path / "comma2" / "escaped.txt"
    result = handler(path=str(target), content="hello")

    assert "rejected" in result.lower()
    call_args = registry.confirmer.call_args
    assert "outside $HOME" in call_args[0][0]
    assert not target.exists()


def test_edit_file_treats_home_prefix_sibling_as_outside_home(tmp_path, monkeypatch):
    fake_home = tmp_path / "comma"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    registry = _make_registry(confirm_result=False)

    from archon.tooling.filesystem_tools import register_filesystem_tools
    register_filesystem_tools(registry)

    calls = registry.register.call_args_list
    edit_call = [c for c in calls if c[0][0] == "edit_file"][0]
    handler = edit_call[0][3]

    target = tmp_path / "comma2" / "escaped.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("old_content")

    result = handler(path=str(target), old="old_content", new="new_content")

    assert "rejected" in result.lower()
    call_args = registry.confirmer.call_args
    assert "outside $HOME" in call_args[0][0]
    assert target.read_text() == "old_content"


def test_write_file_auto_mode_still_confirms_self_modification(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    source_dir = tmp_path / "archon-src"
    source_dir.mkdir()
    registry = _make_registry(
        confirm_result=False,
        permission_mode="auto",
        archon_source_dir=str(source_dir),
    )

    from archon.tooling.filesystem_tools import register_filesystem_tools
    register_filesystem_tools(registry)

    calls = registry.register.call_args_list
    write_call = [c for c in calls if c[0][0] == "write_file"][0]
    handler = write_call[0][3]

    target = source_dir / "agent.py"
    result = handler(path=str(target), content="print('x')")

    assert result == "Self-modification rejected."
    registry.confirmer.assert_called_once()
    call_args = registry.confirmer.call_args
    assert "own source" in call_args[0][0].lower()
    assert not target.exists()
