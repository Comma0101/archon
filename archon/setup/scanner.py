"""Project discovery scanner — reads source files to build an operational profile."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


DISCOVERY_FILES = [
    "README.md", "AGENTS.md",
    "package.json", "pyproject.toml", "setup.py", "setup.cfg",
    "Makefile", "Justfile",
    "docker-compose.yml", "docker-compose.yaml",
    ".env.example", ".env.template", ".env.sample",
    "Cargo.toml", "go.mod",
    ".tool-versions", ".nvmrc", ".python-version",
    "requirements.txt", "Pipfile",
]


@dataclass
class ProjectProfile:
    project_path: str
    project_name: str
    discovery_sources: list[str] = field(default_factory=list)
    source_contents: dict[str, str] = field(default_factory=dict)
    env_vars: list[str] = field(default_factory=list)
    scripts: dict[str, str] = field(default_factory=dict)
    readme_text: str = ""
    stack_hints: list[str] = field(default_factory=list)

    def to_summary(self) -> str:
        lines = [f"Project: {self.project_name}", f"Path: {self.project_path}"]
        if self.discovery_sources:
            lines.append(f"Discovered files: {', '.join(self.discovery_sources)}")
        if self.stack_hints:
            lines.append(f"Stack: {', '.join(self.stack_hints)}")
        if self.scripts:
            lines.append("Scripts:")
            for name, cmd in self.scripts.items():
                lines.append(f"  {name}: {cmd}")
        if self.env_vars:
            lines.append(f"Required env vars: {', '.join(self.env_vars)}")
        if self.readme_text:
            excerpt = self.readme_text[:1500]
            lines.append(f"\nREADME excerpt:\n{excerpt}")
        return "\n".join(lines)


def scan_project(project_path: str) -> ProjectProfile:
    """Scan a project directory and build an operational profile."""
    path = Path(project_path).expanduser().resolve()
    profile = ProjectProfile(
        project_path=str(path),
        project_name=path.name,
    )

    for filename in DISCOVERY_FILES:
        filepath = path / filename
        if filepath.exists() and filepath.is_file():
            try:
                text = filepath.read_text(errors="replace")[:10000]
                profile.discovery_sources.append(filename)
                profile.source_contents[filename] = text
            except Exception:
                continue

    # Extract env vars from .env templates
    for env_file in (".env.example", ".env.template", ".env.sample"):
        text = profile.source_contents.get(env_file, "")
        if text:
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    var_name = line.split("=", 1)[0].strip()
                    if var_name and var_name not in profile.env_vars:
                        profile.env_vars.append(var_name)

    # Extract scripts from package.json
    pkg_text = profile.source_contents.get("package.json", "")
    if pkg_text:
        try:
            pkg = json.loads(pkg_text)
            scripts = pkg.get("scripts", {})
            if isinstance(scripts, dict):
                profile.scripts = {k: str(v) for k, v in scripts.items()}
            deps = set(pkg.get("dependencies", {}).keys()) | set(pkg.get("devDependencies", {}).keys())
            if "next" in deps:
                profile.stack_hints.append("Next.js")
            if "react" in deps:
                profile.stack_hints.append("React")
            if "vue" in deps:
                profile.stack_hints.append("Vue")
        except Exception:
            pass

    # Extract stack hints from pyproject.toml
    pyproject_text = profile.source_contents.get("pyproject.toml", "")
    if pyproject_text:
        profile.stack_hints.append("Python")
        if "fastapi" in pyproject_text.lower():
            profile.stack_hints.append("FastAPI")
        if "django" in pyproject_text.lower():
            profile.stack_hints.append("Django")
        if "flask" in pyproject_text.lower():
            profile.stack_hints.append("Flask")

    if "Cargo.toml" in profile.discovery_sources:
        profile.stack_hints.append("Rust")
    if "go.mod" in profile.discovery_sources:
        profile.stack_hints.append("Go")
    if "requirements.txt" in profile.discovery_sources and "Python" not in profile.stack_hints:
        profile.stack_hints.append("Python")

    profile.readme_text = profile.source_contents.get("README.md", "")

    return profile
