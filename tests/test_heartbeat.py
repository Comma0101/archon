"""Heartbeat runner tests."""

from archon.heartbeat import parse_checklist, ChecklistItem, run_heartbeat, HEARTBEAT_OK


def test_parse_checklist():
    text = """# Heartbeat
- [ ] Check disk space
- [x] Already done task
- [ ] Check blocked setup jobs
"""
    items = parse_checklist(text)
    assert len(items) == 3
    active = [i for i in items if not i.checked]
    assert len(active) == 2
    assert active[0].text == "Check disk space"


def test_parse_empty():
    items = parse_checklist("")
    assert items == []


def test_parse_no_checkboxes():
    items = parse_checklist("# Just a heading\nSome text.\n")
    assert items == []


def test_run_heartbeat_only_notifies_actionable_items():
    replies = [HEARTBEAT_OK, "Disk low on /home"]

    class FakeAgent:
        def run(self, prompt, policy_profile=None):
            return replies.pop(0)

    sent = []
    items = [
        ChecklistItem(text="Check disk space", checked=False, line_number=1),
        ChecklistItem(text="Check blocked setup jobs", checked=False, line_number=2),
    ]

    run_heartbeat(
        items=items,
        agent_factory=FakeAgent,
        notify_fn=sent.append,
    )
    assert len(sent) == 1
    assert "Disk low on /home" in sent[0]


def test_run_heartbeat_no_items():
    sent = []
    run_heartbeat(
        items=[],
        agent_factory=lambda: None,
        notify_fn=sent.append,
    )
    assert sent == []


def test_checked_items_excluded():
    text = "- [x] Done\n- [ ] Active\n"
    items = parse_checklist(text)
    active = [i for i in items if not i.checked]
    assert len(active) == 1
    assert active[0].text == "Active"
