"""System profile gatherer with caching."""

import json
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

from archon.config import CACHE_DIR

CACHE_FILE = CACHE_DIR / "system.json"
CACHE_TTL = 3600  # 1 hour


def _run(cmd: str) -> str:
    try:
        result = subprocess.run(
            ["bash", "-c", cmd],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def gather_profile() -> dict:
    """Gather system information."""
    profile = {
        "os": "Arch Linux",
        "kernel": platform.release(),
        "hostname": platform.node(),
        "arch": platform.machine(),
    }

    # CPU
    cpu_info = _run("lscpu | grep 'Model name' | head -1 | sed 's/.*: *//'")
    cpu_cores = _run("nproc")
    profile["cpu"] = f"{cpu_info} ({cpu_cores} cores)" if cpu_info else f"{cpu_cores} cores"

    # RAM
    ram = _run("free -h | awk '/^Mem:/ {print $2}'")
    profile["ram"] = ram

    # GPU
    gpu = _run("lspci | grep -i 'vga\\|3d' | head -1 | sed 's/.*: //'")
    profile["gpu"] = gpu or "unknown"

    # Shell
    profile["shell"] = os.path.basename(os.environ.get("SHELL", "unknown"))

    # Python
    profile["python"] = platform.python_version()

    # Package count
    pkg_count = _run("pacman -Q 2>/dev/null | wc -l")
    profile["packages"] = pkg_count

    # AUR helper
    for aur in ("yay", "paru", "pikaur", "trizen"):
        if shutil.which(aur):
            profile["aur_helper"] = aur
            break
    else:
        profile["aur_helper"] = "none"

    # Docker
    profile["docker"] = "available" if shutil.which("docker") else "unavailable"

    return profile


def get_profile(force_refresh: bool = False) -> dict:
    """Get system profile, using cache if available."""
    if not force_refresh and CACHE_FILE.exists():
        try:
            data = json.loads(CACHE_FILE.read_text())
            if time.time() - data.get("_timestamp", 0) < CACHE_TTL:
                return data
        except (json.JSONDecodeError, KeyError):
            pass

    profile = gather_profile()
    profile["_timestamp"] = time.time()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(profile, indent=2))
    return profile


def format_profile(profile: dict) -> str:
    """Format profile as a compact string for the system prompt."""
    lines = [
        f"System: {profile.get('os', '?')}, kernel {profile.get('kernel', '?')}",
        f"Host: {profile.get('hostname', '?')}",
        f"CPU: {profile.get('cpu', '?')} | RAM: {profile.get('ram', '?')} | GPU: {profile.get('gpu', '?')}",
        f"Shell: {profile.get('shell', '?')} | Python: {profile.get('python', '?')}",
        f"Packages: ~{profile.get('packages', '?')} (pacman) | AUR: {profile.get('aur_helper', '?')}",
        f"Docker: {profile.get('docker', '?')}",
    ]
    return "\n".join(lines)
