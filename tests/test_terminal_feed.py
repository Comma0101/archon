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
        write_fn=buf.write,
        flush_fn=buf.flush,
        get_buffer_fn=lambda: "use researcher skill",
    )
    feed.set_prompt("you> ")

    feed.emit(ActivityEvent(source="telegram", text="message received"))

    assert buf.render() == (
        "\r\033[K[telegram] message received\n"
        "you> use researcher skill"
    )


def test_terminal_activity_feed_handles_empty_prompt_and_input():
    buf = _Buffer()
    feed = TerminalActivityFeed(
        write_fn=buf.write,
        flush_fn=buf.flush,
    )

    feed.emit(ActivityEvent(source="skill", text="auto-activated: researcher"))

    assert buf.render() == "\r\033[K[skill] auto-activated: researcher\n"
