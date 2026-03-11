"""Heartbeat runner — reads a checklist and runs proactive checks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from archon.config import CONFIG_DIR


HEARTBEAT_PATH = CONFIG_DIR / "heartbeat.md"
HEARTBEAT_OK = "HEARTBEAT_OK"

_CHECKBOX_RE = re.compile(r"^-\s+\[([ xX])\]\s+(.+)$")


@dataclass
class ChecklistItem:
    text: str
    checked: bool
    line_number: int


def parse_checklist(text: str) -> list[ChecklistItem]:
    """Parse markdown checkbox items from a heartbeat file."""
    items: list[ChecklistItem] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        match = _CHECKBOX_RE.match(line.strip())
        if match:
            checked = match.group(1).lower() == "x"
            items.append(ChecklistItem(
                text=match.group(2).strip(),
                checked=checked,
                line_number=lineno,
            ))
    return items


def load_heartbeat_items() -> list[ChecklistItem]:
    """Load active (unchecked) items from the heartbeat file."""
    if not HEARTBEAT_PATH.exists():
        return []
    text = HEARTBEAT_PATH.read_text(errors="replace")
    items = parse_checklist(text)
    return [i for i in items if not i.checked]


def build_heartbeat_prompt(item: ChecklistItem) -> str:
    """Build the agent prompt for a single heartbeat check."""
    return (
        f"Heartbeat check: {item.text}\n\n"
        f"If nothing needs attention, respond exactly: {HEARTBEAT_OK}\n"
        f"If action is needed, take it and report what you did."
    )


def run_heartbeat(
    *,
    items: list[ChecklistItem] | None = None,
    agent_factory,
    notify_fn,
    policy_profile: str = "heartbeat",
) -> None:
    """Run proactive checks and notify only for actionable results."""
    active_items = items if items is not None else load_heartbeat_items()
    for item in active_items:
        agent = agent_factory()
        result = str(agent.run(build_heartbeat_prompt(item), policy_profile=policy_profile) or "").strip()
        if result == HEARTBEAT_OK or not result:
            continue
        notify_fn(f"Heartbeat: {item.text}\n{result}")
