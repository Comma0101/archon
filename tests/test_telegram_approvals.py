"""Tests for Telegram approval helper functions."""

from archon.adapters.telegram_approvals import (
    APPROVAL_ACTION_APPROVE,
    answer_callback_query_safe,
    build_approval_reply_markup,
    build_pending_approval_text,
    looks_like_safety_gate_rejection,
    parse_approval_callback_data,
    truncate_approval_command,
)


class _Bot:
    def __init__(self):
        self.calls = []

    def answer_callback_query(self, callback_query_id, text=None, show_alert=False):
        self.calls.append((callback_query_id, text, show_alert))


class _BadBot:
    def answer_callback_query(self, callback_query_id, text=None, show_alert=False):
        raise RuntimeError("boom")


def test_parse_approval_callback_data_accepts_valid_values():
    parsed = parse_approval_callback_data("appr:abc123:approve")
    assert parsed == ("abc123", APPROVAL_ACTION_APPROVE)


def test_parse_approval_callback_data_rejects_invalid_values():
    assert parse_approval_callback_data(None) is None
    assert parse_approval_callback_data("other:x:y") is None
    assert parse_approval_callback_data("appr:onlytwo") is None
    assert parse_approval_callback_data("appr::approve") is None
    assert parse_approval_callback_data("appr:abc:oops") is None


def test_build_reply_markup_and_prompt_text_include_expected_actions():
    markup = build_approval_reply_markup("id1")
    flat = [btn for row in markup["inline_keyboard"] for btn in row]
    assert any(btn["callback_data"] == "appr:id1:approve" for btn in flat)
    assert any(btn["callback_data"] == "appr:id1:allow15" for btn in flat)
    assert any(btn["callback_data"] == "appr:id1:deny" for btn in flat)
    text = build_pending_approval_text("pacman -Q | head")
    assert "Dangerous action blocked" in text
    assert "Allow 15m" in text


def test_truncate_and_rejection_heuristics():
    long = "x" * 500
    out = truncate_approval_command(long, limit=20)
    assert len(out) == 20
    assert out.endswith("…")
    assert looks_like_safety_gate_rejection("Command rejected by safety gate.")
    assert looks_like_safety_gate_rejection("FORBIDDEN: nope")
    assert not looks_like_safety_gate_rejection("normal output")


def test_answer_callback_query_safe_swallow_errors():
    bot = _Bot()
    answer_callback_query_safe(bot, "cb-1", text="ok")
    assert bot.calls == [("cb-1", "ok", False)]
    answer_callback_query_safe(_BadBot(), "cb-2", text="ignored")
