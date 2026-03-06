"""Self-awareness: reads own source and builds module map."""

import ast
import subprocess
from pathlib import Path


def get_source_dir() -> Path:
    """Resolve the archon package directory."""
    return Path(__file__).parent.resolve()


def is_git_repo(path: Path) -> bool:
    """Check if path is inside a git repository."""
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def get_module_map() -> dict[str, str]:
    """Build a map of module_name -> one_line_description from docstrings."""
    source_dir = get_source_dir()
    modules = {}

    for py_file in sorted(source_dir.glob("*.py")):
        name = py_file.stem
        if name.startswith("_"):
            continue
        try:
            tree = ast.parse(py_file.read_text())
            docstring = ast.get_docstring(tree) or "no description"
            # Take first line only
            modules[name] = docstring.split("\n")[0].strip()
        except Exception:
            modules[name] = "parse error"

    return modules


def format_self_awareness() -> str:
    """Format self-awareness info for the system prompt."""
    source_dir = get_source_dir()
    project_dir = source_dir.parent
    modules = get_module_map()
    git_tracked = is_git_repo(project_dir)

    lines = [
        f"Your source code: {source_dir}/",
        "Modules:",
    ]
    for name, desc in modules.items():
        lines.append(f"  {name}.py - {desc}")

    lines.append("")
    lines.append("Read your code with read_file. Modify with edit_file (requires confirmation).")
    if git_tracked:
        lines.append("All changes are git-tracked.")
    lines.append("safety.py is read-only (cannot self-modify safety).")

    return "\n".join(lines)
