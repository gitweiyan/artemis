import asyncio

from artemis.control.hid import HidControlSink
from artemis.reflex.mouse import TapResult


class FakeController:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def tap(self, x, y):
        self.calls.append((x, y))
        return self.result


class FakeControl:
    def __init__(self):
        self.text = []
        self.events = []

    def type_text(self, text):
        self.text.append(text)

    def mouse_down(self):
        self.events.append("down")

    def mouse_move(self, dx, dy):
        self.events.append((dx, dy))

    def mouse_up(self):
        self.events.append("up")

    def _send_with_response(self, command):
        self.events.append(command)


class FakeSession:
    def __init__(self):
        self.control = FakeControl()
        self.moves = []

    def move(self, dx, dy):
        self.moves.append((dx, dy))
        return (0, 0)


def test_capabilities_declare_relative_hid_with_precheck():
    sink = HidControlSink(FakeSession(), FakeController(TapResult(True, 1, None, 0, "ok")))
    assert sink.capabilities.supports_relative_pointer is True
    assert sink.capabilities.requires_pointer_precheck is True
    assert sink.capabilities.supports_absolute_tap is False


def test_tap_abs_uses_controller_and_reports_success():
    controller = FakeController(TapResult(True, 1, None, 0, "ok"))
    sink = HidControlSink(FakeSession(), controller)

    result = asyncio.run(sink.tap_abs(100, 200))

    assert result.ok is True
    assert controller.calls == [(100, 200)]


def test_failed_tap_maps_to_no_effect():
    controller = FakeController(TapResult(False, 2, None, 9.0, "pointer moved"))
    sink = HidControlSink(FakeSession(), controller)

    result = asyncio.run(sink.tap_abs(100, 200))

    assert result.ok is False
    assert result.incident == "no_effect"
    assert "pointer moved" in result.detail


def test_relative_move_text_and_swipe_delegate():
    session = FakeSession()
    sink = HidControlSink(session, FakeController(TapResult(True, 1, None, 0, "ok")))

    asyncio.run(sink.tap_rel(5, -3))
    asyncio.run(sink.input_text("hi"))
    asyncio.run(sink.swipe(1, 2, 3, 4))

    assert session.moves == [(5, -3)]
    assert session.control.text == ["hi"]
    assert session.control.events[0] == "down"
    assert session.control.events[-1] == "up"
