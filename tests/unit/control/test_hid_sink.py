import asyncio
from types import SimpleNamespace


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


def test_swipe_presses_moves_and_releases_through_the_control():
    """An iOS swipe is a mouse drag: the sink hands the delta to the paced swipe."""

    class SwipeControl(FakeControl):
        def __init__(self):
            super().__init__()
            self.swipes = []

        def swipe_mouse(self, dx, dy, *, duration_ms=400):
            self.swipes.append((dx, dy, duration_ms))

    session = FakeSession()
    session.control = SwipeControl()
    sink = HidControlSink(
        session,
        FakeController(TapResult(True, 1, None, 0, "ok")),
        geometry=__import__("artemis.devices.geometry", fromlist=["ScreenGeometry"]).ScreenGeometry(
            (449, 974), (1080, 2400)
        ),
    )

    result = asyncio.run(sink.swipe(540, 2000, 540, 400, duration_ms=300))

    assert result.ok is True
    # screen pixels -> capture frame pixels, then the drag delta
    assert session.control.swipes and session.control.swipes[0][0] == 0
    assert session.control.swipes[0][1] < 0
    assert session.control.swipes[0][2] == 300


def test_a_failed_swipe_is_an_incident_not_an_exception():
    class BrokenSwipe(FakeControl):
        def swipe_mouse(self, dx, dy, *, duration_ms=400):
            raise RuntimeError("serial write failed")

    session = FakeSession()
    session.control = BrokenSwipe()
    sink = HidControlSink(session, FakeController(TapResult(True, 1, None, 0, "ok")))

    result = asyncio.run(sink.swipe(10, 10, 10, 200))

    assert result.ok is False
    assert result.incident == "swipe_failed"
    assert "serial write failed" in result.detail


# --- iOS drag positioning ------------------------------------------------------------
#
# A relative-pointer swipe is a mouse drag: press, walk the pointer, release. iOS reads
# a drag that starts on an edge as a system gesture (pull down from the top opens
# Control Center, swipe up from the bottom leaves the app), and a pointer parked on an
# edge has no room to drag at all. The sink therefore re-anchors the pointer and starts
# every drag from a fixed point that is safe for its direction.


class PlacingSession(FakeSession):
    """A session with the pointer belief a real ``PointerSession`` carries."""

    def __init__(self, frame_size=(449, 974), gain=(1.48, 1.48), connected=True):
        super().__init__()
        self.frame_size = frame_size
        self.gain_x, self.gain_y = gain
        self.connected = connected
        self.placements = []

    def home(self):
        self.placements.append("home")

    def move(self, dx, dy):
        self.placements.append(("move", dx, dy))
        return (0, 0)


class RecordingSwipeControl(FakeControl):
    def __init__(self, events=None):
        super().__init__()
        self.swipes = []
        self.events = events

    def swipe_mouse(self, dx, dy, *, duration_ms=400):
        self.swipes.append((dx, dy, duration_ms))
        if self.events is not None:
            self.events.append(("swipe", dx, dy, duration_ms))


FRAME = (449, 974)
GAIN = 1.48


def _placing_sink(session=None):
    events = []
    session = session or PlacingSession()
    session.placements = events
    session.control = RecordingSwipeControl(events)
    sink = HidControlSink(session, FakeController(TapResult(True, 1, None, 0, "ok")))
    return sink, session, events


def test_an_upward_swipe_homes_and_starts_low_but_clear_of_the_home_edge():
    sink, _session, events = _placing_sink()

    result = asyncio.run(sink.swipe(100, 900, 100, 300, duration_ms=300))

    assert result.ok is True
    assert events[0] == "home", "a true origin makes the anchor predictable"
    assert events[1] == ("move", round(100 / GAIN), round(FRAME[1] * 0.65 / GAIN))
    assert events[2] == ("swipe", 0, -600, 300)


def test_a_downward_swipe_starts_high_and_a_horizontal_one_in_the_middle():
    sink, _session, events = _placing_sink()

    asyncio.run(sink.swipe(100, 100, 100, 700))
    asyncio.run(sink.swipe(100, 900, 400, 900))

    moves = [event for event in events if isinstance(event, tuple) and event[0] == "move"]
    assert moves[0] == ("move", round(100 / GAIN), round(FRAME[1] * 0.35 / GAIN))
    assert moves[1] == ("move", round(100 / GAIN), round(FRAME[1] * 0.5 / GAIN))


def test_a_disconnected_session_is_connected_before_the_pointer_is_placed():
    session = PlacingSession(connected=False)
    connects = []
    session.connect = lambda: connects.append("connect")
    sink, _session, events = _placing_sink(session)

    asyncio.run(sink.swipe(100, 900, 100, 300))

    assert connects == ["connect"]
    assert events[0] == "home"


def test_a_failed_placement_is_an_incident_not_an_exception():
    class BrokenHome(PlacingSession):
        def home(self):
            raise RuntimeError("serial write failed")

    sink, session, _events = _placing_sink(BrokenHome())

    result = asyncio.run(sink.swipe(100, 900, 100, 300))

    assert result.ok is False
    assert result.incident == "swipe_failed"
    assert "serial write failed" in result.detail
    assert session.control.swipes == []


# --- keyboard-first scrolling --------------------------------------------------------
#
# iOS scroll views scroll on an external keyboard's arrows, which avoids the pointer,
# its calibration and its edge gestures entirely. The swipe therefore tries the arrow
# that matches the direction first and keeps the pointer drag as the fallback for when
# the screen does not move.


class KeyboardSwipeControl(RecordingSwipeControl):
    def __init__(self, events=None):
        super().__init__(events)
        self.keys = []

    def press_named_key(self, name):
        self.keys.append(name)


class Frames:
    """The controller's capture seam: a frame per grab."""

    def __init__(self, frames):
        self._frames = list(frames)
        self.calls = 0

    def grab_latest(self):
        frame = self._frames[min(self.calls, len(self._frames) - 1)]
        self.calls += 1
        return SimpleNamespace(image=frame)


class CapturingController(FakeController):
    def __init__(self, capture):
        super().__init__(TapResult(True, 1, None, 0, "ok"))
        self.capture = capture


def _png(color, size=(64, 64)):
    from io import BytesIO

    from PIL import Image

    out = BytesIO()
    Image.new("RGB", size, color).save(out, "PNG")
    return out.getvalue()


def _keyboard_rig(frames):
    session = PlacingSession()
    control = KeyboardSwipeControl(session.placements)
    session.control = control
    sink = HidControlSink(session, CapturingController(Frames(frames)))
    return sink, session, control


def test_an_upward_swipe_scrolls_with_the_down_key_before_touching_the_pointer():
    sink, session, control = _keyboard_rig(
        [_png((200, 200, 200), FRAME), _png((20, 20, 20), FRAME)]
    )

    result = asyncio.run(sink.swipe(100, 900, 100, 300))

    assert result.ok is True
    assert control.keys == ["down", "down", "down"], "600 frame px is three presses"
    assert control.swipes == [], "a working arrow key replaces the drag"
    assert session.placements == [], "no home or pointer move when the keys scroll"


def test_a_downward_swipe_scrolls_with_the_up_key():
    sink, _session, control = _keyboard_rig(
        [_png((200, 200, 200), FRAME), _png((20, 20, 20), FRAME)]
    )

    asyncio.run(sink.swipe(100, 300, 100, 900))

    assert control.keys == ["up", "up", "up"]
    assert control.swipes == []


def test_a_swipe_falls_back_to_the_drag_when_the_arrow_keys_change_nothing():
    same = _png((200, 200, 200), FRAME)
    sink, session, control = _keyboard_rig([same])

    result = asyncio.run(sink.swipe(100, 900, 100, 300))

    assert result.ok is True
    assert control.keys, "the keyboard was tried first"
    assert control.swipes, "an unchanged screen falls back to the pointer drag"
    assert "home" in session.placements, "the drag still places the pointer first"


def test_a_horizontal_swipe_does_not_try_arrow_keys():
    sink, _session, control = _keyboard_rig(
        [_png((200, 200, 200), FRAME), _png((20, 20, 20), FRAME)]
    )

    asyncio.run(sink.swipe(100, 500, 500, 500))

    assert control.keys == [], "horizontal gestures have no keyboard equivalent here"
    assert control.swipes


def test_a_keyboard_error_falls_back_to_the_drag():
    class BrokenKeys(KeyboardSwipeControl):
        def press_named_key(self, name):
            raise RuntimeError("serial write failed")

    session = PlacingSession()
    control = BrokenKeys(session.placements)
    session.control = control
    sink = HidControlSink(
        session,
        CapturingController(Frames([_png((200, 200, 200), FRAME), _png((20, 20, 20), FRAME)])),
    )

    result = asyncio.run(sink.swipe(100, 900, 100, 300))

    assert result.ok is True
    assert control.swipes, "a device error on the keys must not lose the gesture"


def test_a_focus_ring_is_not_mistaken_for_a_scroll():
    from io import BytesIO

    from PIL import Image, ImageDraw

    def frame(ring: bool) -> bytes:
        image = Image.new("RGB", FRAME, (200, 200, 200))
        if ring:
            ImageDraw.Draw(image).rectangle(
                (10, 10, FRAME[0] - 10, FRAME[1] - 10), outline=(0, 0, 255), width=2
            )
        out = BytesIO()
        image.save(out, "PNG")
        return out.getvalue()

    sink, _session, control = _keyboard_rig([frame(False), frame(True)])

    result = asyncio.run(sink.swipe(100, 900, 100, 300))

    assert result.ok is True
    assert control.keys, "the keys are still tried first"
    assert control.swipes, "a ring-only change still falls back to the drag"


def test_a_tap_reports_the_motion_counts_in_its_metrics():
    """The step record must show how the pointer travelled, not only that it arrived."""
    motion = SimpleNamespace(
        segments=3,
        observations=2,
        phases=["ballistic", "approach"],
        prediction_error_px=6.5,
        global_recoveries=0,
    )
    pointer = SimpleNamespace(x=0.5, y=0.25, confidence=0.9)
    controller = FakeController(TapResult(True, 1, pointer, 3.0, "ok", motion=motion))
    sink = HidControlSink(FakeSession(), controller)

    result = asyncio.run(sink.tap_abs(540, 1200))

    assert result.metrics["motion_segments"] == 3
    assert result.metrics["motion_observations"] == 2
    assert result.metrics["motion_phases"] == "ballistic,approach"
    assert result.metrics["motion_prediction_error_px"] == 6.5
    assert result.metrics["motion_global_recoveries"] == 0


def test_the_drag_start_is_placed_absolutely_when_the_control_supports_it():
    """The firmware's mswipe presses through the absolute report; the drag start must be
    the absolute position it keeps (iOS ignores the relative report's buttons)."""
    calls = []

    class AbsoluteSession:
        frame_size = (449, 974)
        gain_x = 2.0
        gain_y = 2.0
        connected = True

        def __init__(self):
            self.control = SimpleNamespace(mouse_move_abs=lambda x, y: None)

        def home(self):
            calls.append(("home",))

        def move(self, dx, dy):
            calls.append(("move", dx, dy))

        def move_abs(self, x, y):
            calls.append(("abs", x, y))

    sink = HidControlSink(AbsoluteSession(), SimpleNamespace())

    sink._place_for_drag((200, 400), -100)

    assert calls and calls[0][0] == "abs", "one absolute placement, no home/gain dance"
    assert calls[0][1] == 200
