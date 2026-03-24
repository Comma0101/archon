"""Filesystem/basic local tool registrations."""

import difflib
import fnmatch
import queue
import re
import subprocess
import threading
from time import monotonic as _monotonic
from pathlib import Path

from archon.safety import Level, classify

from .common import auto_commit, truncate_text


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        return path.is_relative_to(root)
    except AttributeError:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False


def _should_confirm_write(registry) -> bool:
    """Check if write operations need confirmation based on permission mode."""
    config = getattr(registry, 'config', None)
    if config is None:
        return True
    mode = getattr(getattr(config, 'safety', None), 'permission_mode', 'confirm_all')
    return mode != 'auto'


def _should_confirm_read(registry) -> bool:
    """Check if read-only filesystem operations need confirmation."""
    config = getattr(registry, 'config', None)
    if config is None:
        return True
    mode = getattr(getattr(config, 'safety', None), 'permission_mode', 'confirm_all')
    return mode == 'confirm_all'


def _confirm_read_operation(registry, label: str) -> str | None:
    if _should_confirm_read(registry):
        if not registry.confirmer(label, Level.SAFE):
            return f"{label.split(':', 1)[0]} rejected by safety gate."
    return None


def register_filesystem_tools(registry) -> None:
    # 1. shell
    def shell(command: str, timeout: int = 30, _ctx=None) -> str:
        level = classify(command, registry.archon_source_dir)
        if not registry.confirmer(command, level):
            if _ctx is not None:
                _ctx.meta["blocked"] = True
                _ctx.meta["command_preview"] = command[:240]
            return "Command rejected by safety gate."
        try:
            proc = subprocess.Popen(
                ["bash", "-c", command],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            if proc.stdout is None:
                raise RuntimeError("shell stdout pipe unavailable")

            output_queue: queue.Queue[str | None] = queue.Queue()

            def _reader() -> None:
                try:
                    for raw_line in proc.stdout:
                        output_queue.put(raw_line.rstrip("\n"))
                finally:
                    try:
                        proc.stdout.close()
                    except Exception:
                        pass
                    output_queue.put(None)

            reader_thread = threading.Thread(target=_reader, daemon=True)
            reader_thread.start()

            lines: list[str] = []
            deadline = _monotonic() + timeout
            timed_out = False
            while True:
                remaining = deadline - _monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                try:
                    item = output_queue.get(timeout=min(0.1, remaining))
                except queue.Empty:
                    if proc.poll() is not None and output_queue.empty():
                        continue
                    continue
                if item is None:
                    break
                lines.append(item)
                if _ctx is not None:
                    from archon.ux.events import tool_running

                    _ctx.emit(
                        tool_running(
                            tool="shell",
                            session_id=_ctx.session_id,
                            detail_type="output_line",
                            line=item,
                        )
                    )

            if timed_out:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                reader_thread.join(timeout=1)
                if _ctx is not None:
                    _ctx.meta["exit_code"] = -1
                    _ctx.meta["line_count"] = len(lines)
                body = truncate_text("\n".join(lines), 9800) if lines else ""
                if body:
                    return f"{body}\nError: Command timed out after {timeout}s"
                return f"Error: Command timed out after {timeout}s"

            proc.wait(timeout=5)
            reader_thread.join(timeout=1)
            output = "\n".join(lines)
            body = truncate_text(output, 9800) or "(no output)"
            if _ctx is not None:
                _ctx.meta["exit_code"] = proc.returncode
                _ctx.meta["line_count"] = len(lines)
            return f"{body}\n[exit_code={proc.returncode}]"
        except subprocess.TimeoutExpired:
            if _ctx is not None:
                _ctx.meta["exit_code"] = -1
                _ctx.meta["line_count"] = 0
            return f"Error: Command timed out after {timeout}s"

    registry.register("shell", "Execute a shell command on the system", {
        "properties": {
            "command": {"type": "string", "description": "The shell command to execute"},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default 30)", "default": 30},
        },
        "required": ["command"],
    }, shell)

    # 2. read_file
    def read_file(path: str, offset: int = 0, limit: int = 2000, _ctx=None) -> str:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return f"Error: File not found: {p}"
        if not p.is_file():
            return f"Error: Not a file: {p}"
        rejected = _confirm_read_operation(registry, f"Read file: {p}")
        if rejected is not None:
            if _ctx is not None:
                _ctx.meta["blocked"] = True
                _ctx.meta["command_preview"] = f"read_file: {p}"
            return rejected
        try:
            lines = p.read_text().splitlines()
            selected = lines[offset:offset + limit]
            numbered = [f"{i + offset + 1:>6}\t{line}" for i, line in enumerate(selected)]
            result = "\n".join(numbered)
            if _ctx is not None:
                _ctx.meta["path"] = str(p)
                _ctx.meta["line_count"] = len(selected)
            if len(lines) > offset + limit:
                result += f"\n... ({len(lines) - offset - limit} more lines)"
            return result or "(empty file)"
        except Exception as e:
            return f"Error reading file: {e}"

    registry.register("read_file", "Read a file's contents with line numbers", {
        "properties": {
            "path": {"type": "string", "description": "Absolute path to the file"},
            "offset": {"type": "integer", "description": "Line offset to start from (0-based)", "default": 0},
            "limit": {"type": "integer", "description": "Maximum number of lines to read", "default": 2000},
        },
        "required": ["path"],
    }, read_file)

    # 3. write_file
    def write_file(path: str, content: str, _ctx=None) -> str:
        p = Path(path).expanduser().resolve()
        is_new = not p.exists()
        home = Path.home().resolve()
        source_root = Path(registry.archon_source_dir).resolve() if registry.archon_source_dir else None
        if _should_confirm_write(registry):
            if not _is_relative_to(p, home):
                if not registry.confirmer(f"Write to {p} (outside $HOME)", Level.DANGEROUS):
                    if _ctx is not None:
                        _ctx.meta["blocked"] = True
                        _ctx.meta["command_preview"] = f"write_file: {p}"
                    return "Write rejected by safety gate."
            else:
                if not registry.confirmer(f"Write file: {p}", Level.DANGEROUS):
                    if _ctx is not None:
                        _ctx.meta["blocked"] = True
                        _ctx.meta["command_preview"] = f"write_file: {p}"
                    return "Write rejected by safety gate."
        if source_root and _is_relative_to(p, source_root):
            if not registry.confirmer(f"Write to own source: {p}", Level.DANGEROUS):
                if _ctx is not None:
                    _ctx.meta["blocked"] = True
                    _ctx.meta["command_preview"] = f"write_file: {p}"
                return "Self-modification rejected."
            auto_commit(registry.archon_source_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        if _ctx is not None:
            _ctx.meta["path"] = str(p)
            _ctx.meta["line_count"] = len(content.splitlines())
            _ctx.meta["is_new"] = is_new
        return f"Wrote {len(content)} bytes to {p}"

    registry.register("write_file", "Write content to a file (creates parent dirs)", {
        "properties": {
            "path": {"type": "string", "description": "Absolute path to write to"},
            "content": {"type": "string", "description": "Content to write"},
        },
        "required": ["path", "content"],
    }, write_file)

    # 4. edit_file
    def edit_file(path: str, old: str, new: str, _ctx=None) -> str:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return f"Error: File not found: {p}"
        home = Path.home().resolve()
        source_root = Path(registry.archon_source_dir).resolve() if registry.archon_source_dir else None
        if _should_confirm_write(registry):
            if not _is_relative_to(p, home):
                if not registry.confirmer(f"Edit {p} (outside $HOME)", Level.DANGEROUS):
                    if _ctx is not None:
                        _ctx.meta["blocked"] = True
                        _ctx.meta["command_preview"] = f"edit_file: {p}"
                    return "Edit rejected by safety gate."
            else:
                if not registry.confirmer(f"Edit file: {p}", Level.DANGEROUS):
                    if _ctx is not None:
                        _ctx.meta["blocked"] = True
                        _ctx.meta["command_preview"] = f"edit_file: {p}"
                    return "Edit rejected by safety gate."
        if source_root and _is_relative_to(p, source_root):
            safety_path = source_root / "safety.py"
            if p == safety_path:
                return "FORBIDDEN: Cannot modify safety.py through the agent."
            if not registry.confirmer(f"Edit own source: {p}", Level.DANGEROUS):
                if _ctx is not None:
                    _ctx.meta["blocked"] = True
                    _ctx.meta["command_preview"] = f"edit_file: {p}"
                return "Self-modification rejected."
            auto_commit(registry.archon_source_dir)
        text = p.read_text()
        if old not in text:
            return "Error: old string not found in file"
        count = text.count(old)
        if count > 1:
            return f"Error: old string appears {count} times (must be unique)"
        index = text.index(old)
        line_number = text[:index].count("\n") + 1
        new_text = text.replace(old, new, 1)
        p.write_text(new_text)
        lines_changed = max(old.count("\n"), new.count("\n")) + 1
        if _ctx is not None:
            _ctx.meta["path"] = str(p)
            _ctx.meta["line_number"] = line_number
            _ctx.meta["lines_changed"] = lines_changed
            if len(text) <= 50_000:
                diff_lines = [
                    line
                    for line in difflib.unified_diff(
                        text.splitlines(),
                        new_text.splitlines(),
                        n=1,
                        lineterm="",
                    )
                    if not line.startswith(("---", "+++", "@@"))
                ]
                if diff_lines:
                    from archon.ux.events import tool_diff

                    _ctx.emit(
                        tool_diff(
                            tool="edit_file",
                            session_id=_ctx.session_id,
                            path=str(p),
                            diff_lines=diff_lines,
                            lines_changed=lines_changed,
                        )
                    )
        return f"Edited {p} (replaced 1 occurrence)"

    registry.register("edit_file", "Replace a unique string in a file", {
        "properties": {
            "path": {"type": "string", "description": "Absolute path to the file"},
            "old": {"type": "string", "description": "Exact string to find (must be unique)"},
            "new": {"type": "string", "description": "Replacement string"},
        },
        "required": ["path", "old", "new"],
    }, edit_file)

    # 5. list_dir
    def list_dir(path: str = ".") -> str:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return f"Error: Directory not found: {p}"
        if not p.is_dir():
            return f"Error: Not a directory: {p}"
        rejected = _confirm_read_operation(registry, f"List directory: {p}")
        if rejected is not None:
            return rejected
        entries = sorted(p.iterdir())
        lines = []
        for entry in entries[:500]:
            prefix = "d " if entry.is_dir() else "f "
            lines.append(prefix + entry.name)
        result = "\n".join(lines)
        if len(entries) > 500:
            result += f"\n... ({len(entries) - 500} more entries)"
        return result or "(empty directory)"

    registry.register("list_dir", "List directory contents", {
        "properties": {
            "path": {"type": "string", "description": "Path to list (default: current dir)", "default": "."},
        },
        "required": [],
    }, list_dir)

    # 6. glob
    def glob_files(pattern: str, root: str = ".", limit: int = 200, _ctx=None) -> str:
        root_path = Path(root).expanduser().resolve()
        if not root_path.exists():
            return f"Error: Directory not found: {root_path}"
        if not root_path.is_dir():
            return f"Error: Not a directory: {root_path}"
        rejected = _confirm_read_operation(registry, f"Glob files: {root_path}")
        if rejected is not None:
            if _ctx is not None:
                _ctx.meta["blocked"] = True
                _ctx.meta["command_preview"] = f"glob: {root_path}"
            return rejected
        max_results = max(1, min(int(limit or 200), 1000))
        matches: list[str] = []
        for path in root_path.glob(pattern):
            try:
                resolved = path.resolve()
            except Exception:
                continue
            if resolved.is_file():
                matches.append(str(resolved))
            if len(matches) >= max_results:
                break
        if _ctx is not None:
            _ctx.meta["pattern"] = pattern
            _ctx.meta["file_count"] = len(matches)
        return "\n".join(matches) if matches else "(no matches)"

    registry.register("glob", "Find files under a root using a glob pattern", {
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern to match (e.g. **/*.py)"},
            "root": {"type": "string", "description": "Directory root to search", "default": "."},
            "limit": {"type": "integer", "description": "Maximum number of matches to return", "default": 200},
        },
        "required": ["pattern"],
    }, glob_files)

    # 7. grep
    def grep_files(pattern: str, root: str = ".", glob: str = "", limit: int = 200, _ctx=None) -> str:
        root_path = Path(root).expanduser().resolve()
        if not root_path.exists():
            return f"Error: Directory not found: {root_path}"
        if not root_path.is_dir():
            return f"Error: Not a directory: {root_path}"
        rejected = _confirm_read_operation(registry, f"Grep files: {root_path}")
        if rejected is not None:
            if _ctx is not None:
                _ctx.meta["blocked"] = True
                _ctx.meta["command_preview"] = f"grep: {root_path}"
            return rejected
        max_results = max(1, min(int(limit or 200), 1000))
        try:
            matcher = re.compile(pattern)
        except re.error as e:
            return f"Error: Invalid regex: {e}"

        file_glob = str(glob or "").strip()
        matches: list[str] = []
        file_set: set[str] = set()
        for path in root_path.rglob("*"):
            if len(matches) >= max_results:
                break
            if not path.is_file():
                continue
            if file_glob and not fnmatch.fnmatch(path.name, file_glob):
                continue
            try:
                lines = path.read_text(errors="ignore").splitlines()
            except Exception:
                continue
            for line_no, line in enumerate(lines, start=1):
                if matcher.search(line):
                    resolved_path = str(path.resolve())
                    matches.append(f"{resolved_path}:{line_no}:{line}")
                    file_set.add(resolved_path)
                    if len(matches) >= max_results:
                        break
        if _ctx is not None:
            _ctx.meta["pattern"] = pattern
            _ctx.meta["match_count"] = len(matches)
            _ctx.meta["file_count"] = len(file_set)
        return "\n".join(matches) if matches else "(no matches)"

    registry.register("grep", "Search file contents under a root for a regex pattern", {
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern to search for"},
            "root": {"type": "string", "description": "Directory root to search", "default": "."},
            "glob": {"type": "string", "description": "Optional filename filter (e.g. *.py)", "default": ""},
            "limit": {"type": "integer", "description": "Maximum number of matches to return", "default": 200},
        },
        "required": ["pattern"],
    }, grep_files)
