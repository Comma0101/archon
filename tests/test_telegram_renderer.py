"""Tests for Telegram surface renderer."""

import time

from archon.ux import events
from archon.ux.telegram_renderer import LiveReplyEditor, OutputBatchCollector, TelegramRenderer


def test_render_tool_end_completed():
    renderer = TelegramRenderer()
    evt = events.tool_end("shell", "shell: exit 0 (5 lines)", session_id="s1")
    text = renderer.format_event(evt, status="completed")
    assert "✓" in text
    assert "shell: exit 0 (5 lines)" in text


def test_render_tool_end_failed():
    renderer = TelegramRenderer()
    evt = events.tool_end("shell", "shell: exit 1 (error)", session_id="s1")
    text = renderer.format_event(evt, status="failed")
    assert "✗" in text


def test_render_tool_blocked():
    renderer = TelegramRenderer()
    evt = events.tool_blocked(
        tool="shell",
        session_id="s1",
        command_preview="pacman -Syu",
        safety_level="DANGEROUS",
    )
    text = renderer.format_event(evt)
    assert "Blocked" in text
    assert "pacman -Syu" in text


def test_render_tool_diff_as_code_block():
    renderer = TelegramRenderer()
    evt = events.tool_diff(
        tool="edit_file",
        session_id="s1",
        path="foo.py",
        diff_lines=["-old", "+new"],
        lines_changed=1,
    )
    text = renderer.format_event(evt)
    assert "```diff" in text
    assert "-old" in text
    assert "+new" in text


def test_render_tool_running_output_line():
    renderer = TelegramRenderer()
    evt = events.tool_running(
        tool="shell",
        session_id="s1",
        detail_type="output_line",
        line="building...",
    )
    text = renderer.format_event(evt)
    assert "building..." in text


def test_batch_collector_accumulates_lines():
    sent = []
    collector = OutputBatchCollector(flush_fn=lambda text: sent.append(text), interval_s=0.1)
    collector.add_line("line1")
    collector.add_line("line2")
    assert sent == []
    collector.flush()
    assert len(sent) == 1
    assert "line1" in sent[0]
    assert "line2" in sent[0]


def test_batch_collector_wraps_in_code_block():
    sent = []
    collector = OutputBatchCollector(flush_fn=lambda text: sent.append(text), interval_s=0.1)
    collector.add_line("==> Building...")
    collector.add_line("==> Done")
    collector.flush()
    assert sent[0].startswith("```")
    assert sent[0].endswith("```")


def test_batch_collector_collapses_long_output():
    sent = []
    collector = OutputBatchCollector(flush_fn=lambda text: sent.append(text), interval_s=0.1)
    for i in range(30):
        collector.add_line(f"line {i}")
    collector.flush()
    assert "... (" in sent[0]


def test_live_reply_editor_edits_existing_message():
    sent = []
    edits = []
    editor = LiveReplyEditor(
        send_fn=lambda text: sent.append(text) or {"message_id": 77},
        edit_fn=lambda message_id, text: edits.append((message_id, text)),
        fallback_send_fn=lambda text: sent.append(f"fallback:{text}"),
        throttle_s=0.0,
        min_start_chars=1,
    )

    editor.observe("Hello")
    editor.observe("Hello world")

    assert sent == ["Hello"]
    assert edits == [(77, "Hello world")]
    assert editor.finalize("Hello world") is True


def test_live_reply_editor_falls_back_after_edit_failure():
    sent = []
    editor = LiveReplyEditor(
        send_fn=lambda text: sent.append(text) or {"message_id": 88},
        edit_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("edit failed")),
        fallback_send_fn=lambda text: sent.append(f"fallback:{text}"),
        throttle_s=0.0,
        min_start_chars=1,
    )

    editor.observe("Hello")
    editor.observe("Hello world")

    assert sent == ["Hello", "fallback:Hello world"]
    assert editor.finalize("Hello world") is True
