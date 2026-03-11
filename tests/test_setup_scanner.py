"""Project scanner tests."""

from pathlib import Path
from archon.setup.scanner import scan_project, ProjectProfile


def test_scan_python_project(tmp_path):
    (tmp_path / "README.md").write_text("# My Project\nA Python web app.\n## Setup\npip install -r requirements.txt\n")
    (tmp_path / "requirements.txt").write_text("fastapi\nuvicorn\n")
    (tmp_path / ".env.example").write_text("DATABASE_URL=\nSECRET_KEY=\n")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "myapp"\nrequires-python = ">=3.11"\n')

    profile = scan_project(str(tmp_path))
    assert profile.project_name == tmp_path.name
    assert "README.md" in profile.discovery_sources
    assert "pyproject.toml" in profile.discovery_sources
    assert "DATABASE_URL" in profile.env_vars
    assert "SECRET_KEY" in profile.env_vars


def test_scan_node_project(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "myapp", "scripts": {"dev": "next dev", "build": "next build"}}')
    profile = scan_project(str(tmp_path))
    assert "dev" in profile.scripts
    assert "build" in profile.scripts


def test_scan_empty_project(tmp_path):
    profile = scan_project(str(tmp_path))
    assert profile.project_name == tmp_path.name
    assert len(profile.discovery_sources) == 0


def test_profile_to_summary(tmp_path):
    (tmp_path / "README.md").write_text("# Test\nA test project.\n")
    profile = scan_project(str(tmp_path))
    summary = profile.to_summary()
    assert "Test" in summary or tmp_path.name in summary


def test_scan_rust_project(tmp_path):
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "myapp"\nversion = "0.1.0"\n')
    profile = scan_project(str(tmp_path))
    assert "Rust" in profile.stack_hints


def test_scan_go_project(tmp_path):
    (tmp_path / "go.mod").write_text("module github.com/user/myapp\n\ngo 1.21\n")
    profile = scan_project(str(tmp_path))
    assert "Go" in profile.stack_hints


def test_scan_nextjs_project(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "myapp", "dependencies": {"next": "14.0.0", "react": "18.0.0"}}')
    profile = scan_project(str(tmp_path))
    assert "Next.js" in profile.stack_hints
    assert "React" in profile.stack_hints


def test_env_template_skips_comments(tmp_path):
    (tmp_path / ".env.example").write_text("# This is a comment\nAPI_KEY=\n# Another comment\nDB_HOST=localhost\n")
    profile = scan_project(str(tmp_path))
    assert "API_KEY" in profile.env_vars
    assert "DB_HOST" in profile.env_vars


def test_scan_fastapi_project(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = ["fastapi", "uvicorn"]\n')
    profile = scan_project(str(tmp_path))
    assert "Python" in profile.stack_hints
    assert "FastAPI" in profile.stack_hints
