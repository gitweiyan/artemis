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


def test_failed_tap_preserves_the_controller_incident():
    controller = FakeController(
        TapResult(False, 1, None, None, "pointer not detected", "pointer_missing")
    )
    sink = HidControlSink(FakeSession(), controller)

    result = asyncio.run(sink.tap_abs(100, 200))

    assert result.ok is False
    assert result.incident == "pointer_missing"
    assert "not detected" in result.detail


def test_failed_tap_without_an_incident_still_reports_no_effect():
    controller = FakeController(TapResult(False, 1, None, None, "unknown failure"))
    sink = HidControlSink(FakeSession(), controller)

    result = asyncio.run(sink.tap_abs(100, 200))

    assert result.incident == "no_effect"


def test_tap_abs_converts_screen_pixels_into_capture_frame_pixels():
    """The controller compares against the pointer it sees *in the frame*.

    Callers speak device pixels (the normalized 0-1000 boundary times the screen size),
    while the UVC frame is the cropped phone picture — a different pixel grid.
    """
    from artemis.devices.geometry import ScreenGeometry

    controller = FakeController(TapResult(True, 1, None, 0, "ok"))
    sink = HidControlSink(
        FakeSession(),
        controller,
        geometry=ScreenGeometry((449, 974), (1080, 2400)),
    )

    asyncio.run(sink.tap_abs(540, 1200))

    assert controller.calls == [(224, 487)], "540x1200 on a 1080x2400 screen is the frame center"


def test_tap_abs_without_geometry_passes_the_target_through():
    controller = FakeController(TapResult(True, 1, None, 0, "ok"))
    sink = HidControlSink(FakeSession(), controller)

    asyncio.run(sink.tap_abs(540, 1200))

    assert controller.calls == [(540, 1200)]


def test_press_key_uses_the_named_key_map_and_reports_unsupported_keys():
    class NamedControl(FakeControl):
        def __init__(self):
            super().__init__()
            self.named = []

        def press_named_key(self, name):
            if name == "back":
                raise ValueError("iOS has no Back key: tap the on-screen back arrow instead")
            self.named.append(name)

    session = FakeSession()
    session.control = NamedControl()
    sink = HidControlSink(session, FakeController(TapResult(True, 1, None, 0, "ok")))

    ok = asyncio.run(sink.press_key("enter"))
    failed = asyncio.run(sink.press_key("back"))

    assert ok.ok is True
    assert session.control.named == ["enter"]
    assert failed.ok is False
    assert failed.incident == "unsupported_key"
    assert "back arrow" in failed.detail


def test_a_verified_tap_reports_pointer_evidence():
    """Telemetry needs numbers, not a sentence: where the pointer was and how far off."""
    from types import SimpleNamespace

    pointer = SimpleNamespace(x=0.5, y=0.25, confidence=0.9)
    controller = FakeController(TapResult(True, 1, pointer, 3.0, "ok"))
    sink = HidControlSink(FakeSession(), controller)

    result = asyncio.run(sink.tap_abs(540, 1200))

    metrics = result.metrics
    assert metrics["target_x"] == 540 and metrics["target_y"] == 1200
    assert metrics["pointer_x"] == 500 and metrics["pointer_y"] == 250
    assert metrics["error_px"] == 3.0
    assert metrics["attempts"] == 1


def test_a_refused_tap_keeps_its_evidence():
    from types import SimpleNamespace

    pointer = SimpleNamespace(x=0.1, y=0.2, confidence=0.4)
    controller = FakeController(
        TapResult(False, 2, pointer, 30.0, "not centered", "pointer_not_centered", 1.25)
    )
    sink = HidControlSink(FakeSession(), controller)

    result = asyncio.run(sink.tap_abs(540, 1200))

    assert result.ok is False
    assert result.metrics["error_px"] == 30.0
    assert result.metrics["frame_age_s"] == 1.25
