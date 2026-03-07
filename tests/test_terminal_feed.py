"""Terminal activity feed tests."""

from archon.ux.events import ActivityEvent
from archon.ux.terminal_feed import TerminalActivityFeed


class _Buffer:
    def __init__(self):
        self.parts = []

    def write(self, text: str) -> None:
        self.parts.append(text)

    def flush(self) -> None:
        return None

    def render(self) -> str:
        return "".join(self.parts)


def test_terminal_activity_feed_emits_notice_and_restores_prompt_with_input():
    buf = _Buffer()
    feed = TerminalActivityFeed(
        prompt_fn=lambda: "you> ",
        input_fn=lambda: "use researcher skill",
        write_fn=buf.write,
        flush_fn=buf.flush,
    )

    feed.emit(ActivityEvent(source="telegram", message="message received"))

    assert buf.render() == (
        "\r\033[K[telegram] message received\n"
        "you> use researcher skill"
    )


def test_terminal_activity_feed_handles_empty_prompt_and_input():
    buf = _Buffer()
    feed = TerminalActivityFeed(
        prompt_fn=lambda: "",
        input_fn=lambda: "",
        write_fn=buf.write,
        flush_fn=buf.flush,
    )

    feed.emit(ActivityEvent(source="skill", message="auto-activated: researcher"))

    assert buf.render() == "\r\033[K[skill] auto-activated: researcher\n"


def test_terminal_activity_feed_redacts_and_sanitizes_event_text():
    buf = _Buffer()
    feed = TerminalActivityFeed(
        prompt_fn=lambda: "",
        input_fn=lambda: "",
        write_fn=buf.write,
        flush_fn=buf.flush,
    )

    feed.emit(
        ActivityEvent(
            source="telegram",
            message="OPENAI_API_KEY=sk-live \x1b[31mred\x1b[0m",
        )
    )

    assert buf.render() == "\r\033[K[telegram] OPENAI_API_KEY=[REDACTED] red\n"


def test_terminal_activity_feed_strips_readline_prompt_markers_when_restoring():
    buf = _Buffer()
    feed = TerminalActivityFeed(
        prompt_fn=lambda: "\x01\033[93;1m\x02you>\x01\033[0m\x02 ",
        input_fn=lambda: "draft",
        write_fn=buf.write,
        flush_fn=buf.flush,
    )

    feed.emit(ActivityEvent(source="telegram", message="message received"))

    assert buf.render() == (
        "\r\033[K[telegram] message received\n"
        "you> draft"
    )
