"""Filesystem/basic local tool registrations."""

import fnmatch
import re
import subprocess
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
    def shell(command: str, timeout: int = 30) -> str:
        level = classify(command, registry.archon_source_dir)
        if not registry.confirmer(command, level):
            return "Command rejected by safety gate."
        try:
            result = subprocess.run(
                ["bash", "-c", command],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = result.stdout + result.stderr
            body = truncate_text(output, 9800) or "(no output)"
            if body.endswith("\n"):
                return f"{body}[exit_code={result.returncode}]"
            return f"{body}\n[exit_code={result.returncode}]"
        except subprocess.TimeoutExpired:
            return f"Error: Command timed out after {timeout}s"

    registry.register("shell", "Execute a shell command on the system", {
        "properties": {
            "command": {"type": "string", "description": "The shell command to execute"},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default 30)", "default": 30},
        },
        "required": ["command"],
    }, shell)

    # 2. read_file
    def read_file(path: str, offset: int = 0, limit: int = 2000) -> str:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return f"Error: File not found: {p}"
        if not p.is_file():
            return f"Error: Not a file: {p}"
        rejected = _confirm_read_operation(registry, f"Read file: {p}")
        if rejected is not None:
            return rejected
        try:
            lines = p.read_text().splitlines()
            selected = lines[offset:offset + limit]
            numbered = [f"{i + offset + 1:>6}\t{line}" for i, line in enumerate(selected)]
            result = "\n".join(numbered)
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
    def write_file(path: str, content: str) -> str:
        p = Path(path).expanduser().resolve()
        home = Path.home().resolve()
        source_root = Path(registry.archon_source_dir).resolve() if registry.archon_source_dir else None
        if _should_confirm_write(registry):
            if not _is_relative_to(p, home):
                if not registry.confirmer(f"Write to {p} (outside $HOME)", Level.DANGEROUS):
                    return "Write rejected by safety gate."
            else:
                if not registry.confirmer(f"Write file: {p}", Level.DANGEROUS):
                    return "Write rejected by safety gate."
        if source_root and _is_relative_to(p, source_root):
            if not registry.confirmer(f"Write to own source: {p}", Level.DANGEROUS):
                return "Self-modification rejected."
            auto_commit(registry.archon_source_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"Wrote {len(content)} bytes to {p}"

    registry.register("write_file", "Write content to a file (creates parent dirs)", {
        "properties": {
            "path": {"type": "string", "description": "Absolute path to write to"},
            "content": {"type": "string", "description": "Content to write"},
        },
        "required": ["path", "content"],
    }, write_file)

    # 4. edit_file
    def edit_file(path: str, old: str, new: str) -> str:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return f"Error: File not found: {p}"
        home = Path.home().resolve()
        source_root = Path(registry.archon_source_dir).resolve() if registry.archon_source_dir else None
        if _should_confirm_write(registry):
            if not _is_relative_to(p, home):
                if not registry.confirmer(f"Edit {p} (outside $HOME)", Level.DANGEROUS):
                    return "Edit rejected by safety gate."
            else:
                if not registry.confirmer(f"Edit file: {p}", Level.DANGEROUS):
                    return "Edit rejected by safety gate."
        if source_root and _is_relative_to(p, source_root):
            safety_path = source_root / "safety.py"
            if p == safety_path:
                return "FORBIDDEN: Cannot modify safety.py through the agent."
            if not registry.confirmer(f"Edit own source: {p}", Level.DANGEROUS):
                return "Self-modification rejected."
            auto_commit(registry.archon_source_dir)
        text = p.read_text()
        if old not in text:
            return "Error: old string not found in file"
        count = text.count(old)
        if count > 1:
            return f"Error: old string appears {count} times (must be unique)"
        text = text.replace(old, new, 1)
        p.write_text(text)
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
    def glob_files(pattern: str, root: str = ".", limit: int = 200) -> str:
        root_path = Path(root).expanduser().resolve()
        if not root_path.exists():
            return f"Error: Directory not found: {root_path}"
        if not root_path.is_dir():
            return f"Error: Not a directory: {root_path}"
        rejected = _confirm_read_operation(registry, f"Glob files: {root_path}")
        if rejected is not None:
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
    def grep_files(pattern: str, root: str = ".", glob: str = "", limit: int = 200) -> str:
        root_path = Path(root).expanduser().resolve()
        if not root_path.exists():
            return f"Error: Directory not found: {root_path}"
        if not root_path.is_dir():
            return f"Error: Not a directory: {root_path}"
        rejected = _confirm_read_operation(registry, f"Grep files: {root_path}")
        if rejected is not None:
            return rejected
        max_results = max(1, min(int(limit or 200), 1000))
        try:
            matcher = re.compile(pattern)
        except re.error as e:
            return f"Error: Invalid regex: {e}"

        file_glob = str(glob or "").strip()
        matches: list[str] = []
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
                    matches.append(f"{path.resolve()}:{line_no}:{line}")
                    if len(matches) >= max_results:
                        break
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
