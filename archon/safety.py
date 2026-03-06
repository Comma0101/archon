"""Command classification and TTY confirmation for safe execution."""

import os
import re
import shlex
import sys
import tomllib
from enum import Enum
from pathlib import Path

from archon.config import CONFIG_DIR


class Level(Enum):
    SAFE = "safe"
    DANGEROUS = "dangerous"
    FORBIDDEN = "forbidden"


# Patterns that are always forbidden - catastrophic or irreversible
FORBIDDEN_PATTERNS = [
    re.compile(r"rm\s+(-\w*r\w*\s+.*)?(-\w*f\w*\s+)?/\s*$"),  # rm -rf /
    re.compile(r"rm\s+-\w*r\w*f?\s+/\s*$"),
    re.compile(r":\s*\(\s*\)\s*\{.*\|.*&\s*\}"),  # fork bomb
    re.compile(r"dd\s+.*of=/dev/sd"),  # dd to block devices
    re.compile(r"dd\s+.*of=/dev/nvme"),
    re.compile(r"mkfs\s+/dev/sd"),
    re.compile(r"mkfs\s+/dev/nvme"),
    re.compile(r"mkfs\.\w+\s+/dev/sd"),
    re.compile(r"mkfs\.\w+\s+/dev/nvme"),
    re.compile(r">\s*/dev/sd[a-z]"),  # redirect to block device
    re.compile(r"chmod\s+-R\s+777\s+/\s*$"),
    re.compile(r"chown\s+-R\s+.*\s+/\s*$"),
]

# Binaries/prefixes that are always safe (read-only)
SAFE_COMMANDS = {
    "ls", "cat", "head", "tail", "less", "more", "wc", "file", "stat",
    "find", "locate", "which", "whereis", "type", "whatis",
    "grep", "rg", "ag", "awk", "sed",  # sed is tricky but useful read-only
    "echo", "printf", "date", "cal", "uptime", "whoami", "id", "hostname",
    "uname", "arch", "lsb_release", "hostnamectl",
    "pwd", "realpath", "dirname", "basename",
    "df", "du", "free", "lsblk", "lscpu", "lspci", "lsusb", "lsmem",
    "ip", "ss", "ping", "dig", "nslookup", "host", "curl", "wget",
    "ps", "top", "htop", "pgrep",
    "env", "printenv",
    "tree", "bat", "fd", "eza", "exa",
    "python", "python3", "node", "ruby",  # interpreters (for scripts)
    "jq", "yq", "xq",
    "man", "help", "info",
    "diff", "cmp", "comm",
    "sort", "uniq", "tr", "cut", "paste", "column",
    "tee", "xargs",
    "true", "false", "test",
}

# Subcommand patterns that make a command safe
SAFE_SUBCOMMANDS = {
    "pacman": {"-Q", "-Qi", "-Ql", "-Qs", "-Si", "-Ss", "-F"},
    "systemctl": {"status", "is-active", "is-enabled", "list-units",
                  "list-unit-files", "show", "cat"},
    "git": {"log", "status", "diff", "show", "branch", "remote", "tag",
            "stash list", "ls-files", "rev-parse", "describe", "shortlog",
            "blame", "config --list", "config --get"},
    "docker": {"ps", "images", "logs", "inspect", "info", "version",
               "stats", "top", "port", "network ls", "volume ls"},
    "journalctl": set(),  # always safe (read-only)
    "timedatectl": {"status", "show"},
    "localectl": {"status"},
    "cargo": {"check", "clippy", "doc", "test", "bench", "tree"},
    "npm": {"list", "ls", "outdated", "audit", "view", "info"},
    "pip": {"list", "show", "freeze", "check"},
    "uv": {"pip list", "pip show", "pip freeze"},
}

# Commands that are always dangerous
DANGEROUS_COMMANDS = {
    "sudo", "su", "doas",
    "rm", "rmdir", "shred",
    "kill", "killall", "pkill",
    "reboot", "shutdown", "poweroff", "halt",
    "mount", "umount",
    "useradd", "userdel", "usermod", "groupadd", "groupdel",
    "passwd", "chpasswd",
    "iptables", "nft", "firewall-cmd",
    "crontab",
}

DANGEROUS_SUBCOMMANDS = {
    "pacman": {"-S", "-Sy", "-Syu", "-R", "-Rs", "-Rns", "-U"},
    "systemctl": {"start", "stop", "restart", "enable", "disable",
                  "mask", "unmask", "daemon-reload"},
    "git": {"push", "reset", "checkout", "rebase", "merge", "cherry-pick",
            "clean", "rm"},
    "docker": {"run", "exec", "rm", "rmi", "stop", "kill", "build",
               "pull", "push", "compose"},
    "pip": {"install", "uninstall"},
    "npm": {"install", "uninstall", "update"},
    "cargo": {"install"},
    "uv": {"pip install", "pip uninstall"},
}


def _load_user_rules() -> tuple[set[str], set[str]]:
    """Load custom allow/deny rules from safety.toml."""
    safety_file = CONFIG_DIR / "safety.toml"
    if not safety_file.exists():
        return set(), set()
    with open(safety_file, "rb") as f:
        data = tomllib.load(f)
    allow = set(data.get("allow", []))
    deny = set(data.get("deny", []))
    return allow, deny


def classify(command: str, archon_source_dir: str | None = None) -> Level:
    """Classify a shell command as SAFE, DANGEROUS, or FORBIDDEN."""
    stripped = command.strip()

    # Check forbidden patterns first
    for pattern in FORBIDDEN_PATTERNS:
        if pattern.search(stripped):
            return Level.FORBIDDEN

    # Self-modification guard: editing safety.py is FORBIDDEN
    if archon_source_dir:
        safety_path = os.path.join(archon_source_dir, "safety.py")
        if safety_path in stripped:
            return Level.FORBIDDEN

    # Handle pipes: classify each segment, return worst
    if "|" in stripped:
        segments = stripped.split("|")
        levels = [classify(seg.strip(), archon_source_dir) for seg in segments]
        if Level.FORBIDDEN in levels:
            return Level.FORBIDDEN
        if Level.DANGEROUS in levels:
            return Level.DANGEROUS
        return Level.SAFE

    # Handle && and ; chains
    for sep in ["&&", ";"]:
        if sep in stripped:
            segments = stripped.split(sep)
            levels = [classify(seg.strip(), archon_source_dir) for seg in segments]
            if Level.FORBIDDEN in levels:
                return Level.FORBIDDEN
            if Level.DANGEROUS in levels:
                return Level.DANGEROUS
            return Level.SAFE

    # Parse the command
    try:
        parts = shlex.split(stripped)
    except ValueError:
        return Level.DANGEROUS  # unparseable = assume dangerous

    if not parts:
        return Level.SAFE

    binary = os.path.basename(parts[0])

    # Self-modification guard: editing own source is DANGEROUS
    if archon_source_dir and binary in ("nano", "vim", "nvim", "vi", "code", "edit"):
        if any(archon_source_dir in arg for arg in parts[1:]):
            return Level.DANGEROUS

    # Check user allow/deny lists
    user_allow, user_deny = _load_user_rules()
    if stripped in user_deny or binary in user_deny:
        return Level.FORBIDDEN
    if stripped in user_allow or binary in user_allow:
        return Level.SAFE

    # Special-case read-mostly tools with destructive flags.
    if binary == "sed" and _sed_has_in_place_flag(parts[1:]):
        return Level.DANGEROUS

    # Check safe commands
    if binary in SAFE_COMMANDS:
        return Level.SAFE

    # Check subcommand-based rules
    if binary in SAFE_SUBCOMMANDS:
        safe_subs = SAFE_SUBCOMMANDS[binary]
        if not safe_subs:  # empty set means always safe
            return Level.SAFE
        rest = " ".join(parts[1:])
        for sub in safe_subs:
            if rest.startswith(sub) or sub in parts[1:2]:
                return Level.SAFE

    if binary in DANGEROUS_SUBCOMMANDS:
        rest = " ".join(parts[1:])
        for sub in DANGEROUS_SUBCOMMANDS[binary]:
            if rest.startswith(sub) or sub in parts[1:2]:
                return Level.DANGEROUS

    # Check always-dangerous binaries
    if binary in DANGEROUS_COMMANDS:
        return Level.DANGEROUS

    # Default: confirm
    return Level.DANGEROUS


def _sed_has_in_place_flag(args: list[str]) -> bool:
    """Detect GNU/BSD sed in-place editing flags (e.g. `-i`, `-Ei`, `--in-place`)."""
    for arg in args:
        if arg == "--":
            break
        if not arg or arg == "-":
            continue
        if arg.startswith("--"):
            if arg == "--in-place" or arg.startswith("--in-place="):
                return True
            continue
        if not arg.startswith("-"):
            # sed options normally appear before the script/file operands.
            break
        short = arg[1:]
        if not short:
            continue
        # `-i`, `-i.bak`
        if short.startswith("i"):
            return True
        # Combined short flags like `-Ei` or `-nri`
        if short.isalpha() and "i" in short:
            return True
    return False


def confirm(command: str, level: Level) -> bool:
    """Ask user for confirmation via TTY. Returns True if approved."""
    if level == Level.SAFE:
        return True
    if level == Level.FORBIDDEN:
        print(f"\n\033[91mFORBIDDEN\033[0m: {command}")
        print("This command is blocked for safety reasons.")
        return False

    # DANGEROUS: ask for confirmation
    if not sys.stdin.isatty():
        print(f"\n\033[93mBLOCKED\033[0m: {command}")
        print("Cannot confirm in non-interactive mode.")
        return False

    print(f"\n\033[93mCONFIRM\033[0m: {command}")
    try:
        response = input("Execute? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return response in ("y", "yes")
