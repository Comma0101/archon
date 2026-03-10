from archon.control.contracts import HookEvent
from archon.control.hooks import HookBus


def test_hook_bus_records_handler_failures_without_blocking_later_handlers():
    bus = HookBus()
    seen = []

    def broken_handler(_event):
        raise RuntimeError("boom")

    def good_handler(event):
        seen.append(event.kind)

    bus.register("demo.event", broken_handler)
    bus.register("demo.event", good_handler)

    bus.emit(HookEvent(kind="demo.event", payload={"x": 1}))

    assert seen == ["demo.event"]
    failures = bus.get_failures()
    assert len(failures) == 1
    assert failures[0]["kind"] == "demo.event"
    assert failures[0]["error_type"] == "RuntimeError"
    assert failures[0]["error"] == "boom"
