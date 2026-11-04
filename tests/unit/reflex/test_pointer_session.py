"""P2b: pointer state across one iOS mouse connection.

iOS starts the pointer at the top-left, keeps a last position even when the cursor
is hidden, and clicks at that position. The session therefore uses a known origin
plus calibrated deltas instead of locating the pointer for every action.
"""

from __future__ import annotations

import pytest

from artemis.reflex.mouse import PointerSession


class FakeControl:
    def __init__(self):
        self.preflights = 0
        self.homes = 0
        self.ios_homes = 0
        self.events: list[str] = []
        self.moves: list[tuple[int, int]] = []
        self.clicks = 0

    def preflight(self):
        self.preflights += 1
        return {"mounted": 1, "suspended": 0, "mouse": 1, "keyboard": 1, "touch": 0, "mode": "ios"}

    def ios_home(self):
        self.ios_homes += 1
        self.events.append("ios_home")

    def mouse_home(self):
        self.homes += 1
        self.events.append("mouse_home")

    def mouse_move(self, dx, dy):
        self.moves.append((int(dx), int(dy)))

    def mouse_click(self):
        self.clicks += 1


def _session(control=None):
    control = control or FakeControl()
    return PointerSession(
        control, frame_size=(449, 974), gain=(2.0, 2.0), home_settle_s=0.0
    ), control


def test_connect_preflights_homes_and_starts_at_the_top_left():
    session, control = _session()

    session.connect()

    assert control.preflights == 1
    assert control.ios_homes == 1
    assert control.homes == 1
    assert control.events[:2] == ["ios_home", "mouse_home"], "iOS Home before pointer home"
    assert session.position == (0.0, 0.0)
    assert session.connected is True


def test_move_updates_state_using_the_calibrated_gain():
    session, control = _session()
    session.connect()

    session.move(10, 20)

    assert control.moves == [(10, 20)]
    assert session.position == (20.0, 40.0)


def test_move_clamps_at_the_frame_edges():
    session, _ = _session()
    session.connect()

    session.move(1000, 1000)

    assert session.position == (449.0, 974.0)


def test_tap_computes_the_delta_from_the_current_state_and_clicks():
    session, control = _session()
    session.connect()

    deltas = session.tap(200, 100)

    assert deltas == (100, 50)
    assert control.moves == [(100, 50)]
    assert control.clicks == 1
    assert session.position == (200.0, 100.0)


def test_tap_at_the_current_position_clicks_without_a_zero_move():
    session, control = _session()
    session.connect()
    session.observe(200, 100)

    deltas = session.tap(200, 100)

    assert deltas == (0, 0)
    assert control.moves == []
    assert control.clicks == 1


def test_home_reanchors_the_state_to_zero():
    session, control = _session()
    session.connect()
    session.move(100, 100)

    session.home()

    assert control.homes == 2
    assert session.position == (0.0, 0.0)


def test_observe_corrects_the_dead_reckoned_state():
    session, _ = _session()
    session.connect()
    session.move(10, 10)

    session.observe(300, 400)

    assert session.position == (300.0, 400.0)


def test_calibrate_fits_gain_per_axis_from_home_samples():
    session, _ = _session()

    session.calibrate(
        [
            (100, 0, 140, 0),
            (200, 0, 280, 0),
            (0, 100, 0, 150),
            (0, 200, 0, 300),
        ]
    )

    assert session.gain_x == pytest.approx(1.4)
    assert session.gain_y == pytest.approx(1.5)
    assert session.position == (0.0, 0.0)


def test_actions_before_connect_are_refused():
    session, control = _session()

    with pytest.raises(RuntimeError):
        session.tap(10, 10)

    assert control.moves == [] and control.clicks == 0


def test_a_target_outside_the_frame_is_rejected():
    session, _ = _session()
    session.connect()

    with pytest.raises(ValueError):
        session.tap(500, 100)


class FakeCapture:
    """A camera with a scripted frame list.

    When a control is supplied, the screen changes once a click has been delivered, so a
    test does not have to predict how many frames the servo loop consumes before it
    clicks.
    """

    def __init__(self, frames: list[bytes], control=None):
        self.frames = frames
        self.control = control
        self.index = 0

    def grab_latest(self, timeout_s=None):
        from types import SimpleNamespace

        if self.control is not None and self.control.clicks:
            return SimpleNamespace(image=_png((240, 40, 40)))
        frame = self.frames[min(self.index, len(self.frames) - 1)]
        self.index += 1
        return SimpleNamespace(image=frame)


def _png(color: tuple[int, int, int]) -> bytes:
    from io import BytesIO

    from PIL import Image

    out = BytesIO()
    Image.new("RGB", (100, 100), color).save(out, "PNG")
    return out.getvalue()


def test_stateful_tap_clicks_without_pointer_detection_and_verifies():
    from artemis.reflex.mouse import SessionTapController

    control = FakeControl()
    session = PointerSession(control, frame_size=(200, 200), gain=(2.0, 2.0))
    session.connect()
    capture = FakeCapture([_png((120, 120, 120)), _png((240, 40, 40))])
    controller = SessionTapController(session, capture=capture, settle_timeout_s=0.0)

    result = controller.tap(200, 100)

    assert result.ok is True
    assert control.moves == [(100, 50)]
    assert control.clicks == 1


def test_failed_click_reanchors_and_retries():
    from artemis.reflex.mouse import SessionTapController

    control = FakeControl()
    session = PointerSession(control, frame_size=(200, 200), gain=(2.0, 2.0))
    session.connect()
    capture = FakeCapture([_png((120, 120, 120))] * 10)
    controller = SessionTapController(
        session, capture=capture, max_attempts=2, settle_timeout_s=0.0
    )

    result = controller.tap(200, 100)

    assert result.ok is False
    assert control.clicks == 2
    assert control.homes == 1 + 2, "connect + one re-anchor after each failed click"
    assert control.ios_homes == 1 + 2, "each re-anchor also returns to the iOS Home screen"


def test_stateful_tap_without_capture_reports_ack_success_only():
    from artemis.reflex.mouse import SessionTapController

    control = FakeControl()
    session = PointerSession(control, frame_size=(200, 200), gain=(2.0, 2.0))
    session.connect()
    controller = SessionTapController(session, capture=None)

    result = controller.tap(200, 100)

    assert result.ok is True
    assert control.clicks == 1
    assert result.message.startswith("clicked")


def test_ensure_ios_home_does_not_move_the_pointer():
    session, control = _session()
    session.connect()
    before = session.position

    session.ensure_home_screen()

    assert session.position == before
    assert control.ios_homes == 2


class FakeDetector:
    def __init__(self, readings):
        self.readings = list(readings)
        self.calls = 0

    def detect(self, image, near=None):
        self.calls += 1
        if not self.readings:
            return None
        return self.readings.pop(0)


def test_verified_tap_clicks_only_after_pointer_is_at_the_target():
    from artemis.reflex.mouse import SessionTapController
    from artemis.reflex.vision import Pointer

    control = FakeControl()
    session = PointerSession(control, frame_size=(200, 200), gain=(2.0, 2.0))
    session.connect()
    capture = FakeCapture([_png((120, 120, 120))] * 10, control=control)
    detector = FakeDetector([Pointer(0.5, 0.5, 0.95, "fake")] * 5)
    controller = SessionTapController(
        session, capture=capture, detector=detector, settle_timeout_s=0.0
    )

    result = controller.tap(100, 100)

    assert result.ok is True
    assert control.clicks == 1
    assert control.moves, "the first leg is planned, not a full dead-reckoned travel"


def test_verified_tap_corrects_an_offset_pointer_before_clicking():
    from artemis.reflex.mouse import SessionTapController
    from artemis.reflex.vision import Pointer

    control = FakeControl()
    session = PointerSession(control, frame_size=(200, 200), gain=(2.0, 2.0))
    session.connect()
    capture = FakeCapture([_png((120, 120, 120))] * 10, control=control)
    detector = FakeDetector(
        [Pointer(0.25, 0.25, 0.95, "fake")] + [Pointer(0.5, 0.5, 0.95, "fake")] * 5
    )
    controller = SessionTapController(
        session, capture=capture, detector=detector, settle_timeout_s=0.0
    )

    result = controller.tap(100, 100)

    assert result.ok is True
    assert control.moves, "the controller must correct before clicking"
    assert control.clicks == 1


def test_verified_tap_refuses_to_click_when_pointer_is_not_detected():
    from artemis.reflex.mouse import SessionTapController

    control = FakeControl()
    session = PointerSession(control, frame_size=(200, 200), gain=(2.0, 2.0))
    session.connect()
    capture = FakeCapture([_png((120, 120, 120))])
    detector = FakeDetector([None])
    controller = SessionTapController(
        session, capture=capture, detector=detector, settle_timeout_s=0.0
    )

    result = controller.tap(100, 100)

    assert result.ok is False
    assert control.clicks == 0, "no pre-click verification means no click"
    assert "not detected" in result.message


def test_verified_tap_fails_when_click_does_not_change_the_screen():
    from artemis.reflex.mouse import SessionTapController
    from artemis.reflex.vision import Pointer

    control = FakeControl()
    session = PointerSession(control, frame_size=(200, 200), gain=(2.0, 2.0))
    session.connect()
    capture = FakeCapture([_png((120, 120, 120)), _png((120, 120, 120))])
    detector = FakeDetector([Pointer(0.5, 0.5, 0.95, "fake")])
    controller = SessionTapController(
        session, capture=capture, detector=detector, settle_timeout_s=0.0
    )

    result = controller.tap(100, 100)

    assert result.ok is False
    assert control.clicks == 1
    assert "did not change" in result.message


def test_verified_tap_reports_pointer_missing_incident():
    from artemis.reflex.mouse import SessionTapController

    control = FakeControl()
    session = PointerSession(control, frame_size=(200, 200), gain=(2.0, 2.0))
    session.connect()
    capture = FakeCapture([_png((120, 120, 120))])
    detector = FakeDetector([None])
    controller = SessionTapController(
        session, capture=capture, detector=detector, settle_timeout_s=0.0
    )

    result = controller.tap(100, 100)

    assert result.ok is False
    assert result.incident == "pointer_missing"
    assert control.clicks == 0


def test_verified_tap_reports_no_effect_incident():
    from artemis.reflex.mouse import SessionTapController
    from artemis.reflex.vision import Pointer

    control = FakeControl()
    session = PointerSession(control, frame_size=(200, 200), gain=(2.0, 2.0))
    session.connect()
    capture = FakeCapture([_png((120, 120, 120)), _png((120, 120, 120))])
    detector = FakeDetector([Pointer(0.5, 0.5, 0.95, "fake")])
    controller = SessionTapController(
        session, capture=capture, detector=detector, settle_timeout_s=0.0
    )

    result = controller.tap(100, 100)

    assert result.ok is False
    assert result.incident == "no_effect"


def test_verified_tap_reports_not_centered_incident_when_pointer_never_converges():
    from artemis.reflex.mouse import SessionTapController
    from artemis.reflex.vision import Pointer

    control = FakeControl()
    session = PointerSession(control, frame_size=(200, 200), gain=(2.0, 2.0))
    session.connect()
    capture = FakeCapture([_png((120, 120, 120))] * 50)
    detector = FakeDetector([Pointer(0.1, 0.1, 0.95, "fake")] * 50)
    controller = SessionTapController(
        session,
        capture=capture,
        detector=detector,
        max_attempts=1,
        max_steps=2,
        settle_timeout_s=0.0,
    )

    result = controller.tap(100, 100)

    assert result.ok is False
    assert result.incident == "pointer_not_centered"
    assert control.clicks == 0


def test_a_transient_miss_is_retried_before_declaring_pointer_missing():
    """The AssistiveTouch ring fades and re-appears; one empty frame is not proof."""
    from artemis.reflex.mouse import SessionTapController
    from artemis.reflex.vision import Pointer

    control = FakeControl()
    session = PointerSession(control, frame_size=(200, 200), gain=(2.0, 2.0))
    session.connect()
    capture = FakeCapture([_png((120, 120, 120))] * 10, control=control)
    detector = FakeDetector([None, Pointer(0.5, 0.5, 0.95, "fake")] * 5)
    controller = SessionTapController(
        session, capture=capture, detector=detector, settle_timeout_s=0.0
    )

    result = controller.tap(100, 100)

    assert detector.calls >= 2, "the controller must re-observe before giving up"
    assert result.ok is True
    assert control.clicks == 1


def test_pointer_missing_only_after_every_observation_attempt():
    from artemis.reflex.mouse import SessionTapController

    control = FakeControl()
    session = PointerSession(control, frame_size=(200, 200), gain=(2.0, 2.0))
    session.connect()
    capture = FakeCapture([_png((120, 120, 120))] * 5)
    detector = FakeDetector([None, None, None, None])
    controller = SessionTapController(
        session,
        capture=capture,
        detector=detector,
        observe_attempts=3,
        settle_timeout_s=0.0,
    )

    result = controller.tap(100, 100)

    # Two rounds of observe_attempts (the initial look, then the look after a wake
    # move); each attempt tries the full frame and then the corridor crop, so two
    # detector calls per attempt.
    assert detector.calls == 12
    assert control.moves, "the wake move happened before giving up"
    assert result.incident == "pointer_missing"
    assert control.clicks == 0


def test_a_faded_pointer_is_woken_by_a_move_before_the_first_observation_is_trusted():
    """iOS hides the AssistiveTouch pointer; a deliberate move reveals it again."""
    from artemis.reflex.mouse import SessionTapController
    from artemis.reflex.vision import Pointer

    class WakeDetector:
        def __init__(self, control):
            self.control = control
            self.calls = 0

        def detect(self, image, near=None):
            self.calls += 1
            if not self.control.moves:
                return None
            return Pointer(0.5, 0.5, 0.95, "fake")

    control = FakeControl()
    session = PointerSession(control, frame_size=(200, 200), gain=(2.0, 2.0))
    session.connect()
    capture = FakeCapture([_png((120, 120, 120)), _png((240, 40, 40))] * 20)
    controller = SessionTapController(
        session, capture=capture, detector=WakeDetector(control), settle_timeout_s=0.0
    )

    result = controller.tap(100, 100)

    assert control.moves, "a wake move is required before the pointer can be seen"
    assert result.ok is True
    assert control.clicks == 1


def test_a_pointer_that_stays_invisible_after_a_wake_is_never_clicked():
    from artemis.reflex.mouse import SessionTapController

    control = FakeControl()
    session = PointerSession(control, frame_size=(200, 200), gain=(2.0, 2.0))
    session.connect()
    capture = FakeCapture([_png((120, 120, 120))] * 20)
    detector = FakeDetector([None] * 40)
    controller = SessionTapController(
        session, capture=capture, detector=detector, settle_timeout_s=0.0
    )

    result = controller.tap(100, 100)

    assert result.incident == "pointer_missing"
    assert control.clicks == 0


def test_frame_after_skips_the_frame_that_predates_the_move():
    """A queued frame describes the pointer position *before* the move."""
    from artemis.reflex.mouse import SessionTapController

    class LaggingCapture:
        def __init__(self):
            self.frames = [_png((120, 120, 120)), _png((130, 120, 120))]
            self.index = 0

        def grab_latest(self):
            from types import SimpleNamespace

            frame = self.frames[min(self.index, len(self.frames) - 1)]
            self.index += 1
            return SimpleNamespace(image=frame)

    control = FakeControl()
    session = PointerSession(control, frame_size=(200, 200), gain=(2.0, 2.0))
    session.connect()
    before = _png((120, 120, 120))
    controller = SessionTapController(
        session, capture=LaggingCapture(), detector=FakeDetector([]), settle_timeout_s=0.2
    )

    frame = controller._frame_after(before)

    assert frame.image != before


def test_the_frame_used_after_a_move_reflects_that_move():
    """A camera lags the pointer: re-detecting on the pre-move frame makes the loop
    chase its own tail and never converge (measured live 2026-10-21)."""
    from artemis.reflex.mouse import SessionTapController
    from artemis.reflex.vision import Pointer

    frames = [_png((120, 120, 120)), _png((130, 120, 120)), _png((150, 120, 120))]

    class LaggingCapture:
        """Some frames still show the state from before the last move."""

        def __init__(self, control):
            self.control = control
            self.index = 0
            self.frames = frames + [_png((200, 200, 200))] * 20

        def grab_latest(self):
            from types import SimpleNamespace

            # After the click the screen changed (a tapped app pushes a new page).
            if self.control.clicks:
                return SimpleNamespace(image=_png((40, 200, 40)))
            frame = self.frames[min(self.index, len(self.frames) - 1)]
            self.index += 1
            return SimpleNamespace(image=frame)

    class ConvergingDetector:
        def __init__(self):
            self.calls = 0

        def detect(self, image, near=None):
            self.calls += 1
            # Only once the frame has changed after a move is the pointer "arrived".
            if self.calls == 1:
                return Pointer(0.0, 0.0, 0.95, "fake")
            return Pointer(0.5, 0.5, 0.95, "fake")

    control = FakeControl()
    session = PointerSession(control, frame_size=(200, 200), gain=(2.0, 2.0))
    session.connect()
    controller = SessionTapController(
        session,
        capture=LaggingCapture(control),
        detector=ConvergingDetector(),
        max_steps=3,
        settle_timeout_s=0.2,
    )

    result = controller.tap(100, 100)

    assert result.ok is True, result.message
    assert control.clicks == 1


def test_an_old_frame_is_never_used_to_verify_a_click():
    """A frozen capture path used to look like a slow pointer; a stale frame is a stop.

    Measured live 2026-10-21: a leftover ffmpeg held the capture card, every "frame"
    was the same frozen image, and the loop happily verified a pointer that had not
    been seen for seconds.
    """
    import time

    from artemis.reflex.mouse import SessionTapController
    from artemis.reflex.vision import Pointer

    class OldFrameCapture:
        def __init__(self):
            self.image = _png((120, 120, 120))

        def grab_latest(self):
            from types import SimpleNamespace

            return SimpleNamespace(image=self.image, captured_at=time.monotonic() - 30.0)

    control = FakeControl()
    session = PointerSession(control, frame_size=(200, 200), gain=(2.0, 2.0))
    session.connect()
    controller = SessionTapController(
        session,
        capture=OldFrameCapture(),
        detector=FakeDetector([Pointer(0.5, 0.5, 0.95, "fake")] * 5),
        settle_timeout_s=0.0,
        max_frame_age_s=3.0,
    )

    result = controller.tap(100, 100)

    assert result.ok is False
    assert result.incident == "stale_frame"
    assert control.clicks == 0


def test_a_frame_without_a_timestamp_is_accepted():
    """A source that cannot date its frames must not be blocked by the freshness gate."""
    from artemis.reflex.mouse import SessionTapController
    from artemis.reflex.vision import Pointer

    control = FakeControl()
    session = PointerSession(control, frame_size=(200, 200), gain=(2.0, 2.0))
    session.connect()
    capture = FakeCapture([_png((120, 120, 120))] * 10, control=control)
    controller = SessionTapController(
        session,
        capture=capture,
        detector=FakeDetector([Pointer(0.5, 0.5, 0.95, "fake")] * 5),
        settle_timeout_s=0.0,
    )

    assert controller.tap(100, 100).ok is True


def _dot_png(x: int, y: int, size=(360, 640)) -> bytes:
    """A frame with one small dark dot — stands in for the AssistiveTouch pointer."""
    import cv2
    import numpy as np

    image = np.full((size[1], size[0], 3), 235, dtype=np.uint8)
    cv2.circle(image, (x, y), 12, (90, 90, 90), 4)
    return cv2.imencode(".png", image)[1].tobytes()


def test_a_pointer_the_colour_detector_cannot_see_is_found_by_the_nudge_diff():
    """Red-ring detection misses a grey pointer; the nudge/diff path still sees it.

    This is the method the project's iOS HID knowledge note prescribes (move, diff the
    two frames, the moved blob is the pointer), and it needs no colour or shape model.
    """
    from artemis.reflex.mouse import SessionTapController
    from artemis.reflex.vision import Pointer, PointerTracker

    nudge, gain = 20, 2.0
    start = (60, 200)

    class MovingCapture:
        """The dot follows the moves the controller makes, like a real pointer."""

        def __init__(self):
            self.x, self.y = start
            self.before = None

        def grab_latest(self):
            from types import SimpleNamespace

            return SimpleNamespace(image=_dot_png(self.x, self.y))

        def apply_move(self, dx, dy):
            self.x += int(dx * gain)
            self.y += int(dy * gain)

    capture = MovingCapture()

    class NoColour:
        def detect(self, image, near=None):
            return None

    class MovingControl(FakeControl):
        def mouse_move(self, dx, dy):
            super().mouse_move(dx, dy)
            capture.apply_move(dx, dy)

        def mouse_home(self):
            super().mouse_home()

    control = MovingControl()
    session = PointerSession(control, frame_size=(360, 640), gain=(gain, gain))
    session.connect()
    # connect() homes the (fake) pointer to the corner; park it at the test's start.
    capture.x, capture.y = start
    session.observe(*start)
    controller = SessionTapController(
        session,
        capture=capture,
        detector=NoColour(),
        tracker=PointerTracker(),
        settle_timeout_s=0.0,
        max_frame_age_s=0.0,
    )

    frame, pointer = controller._observe()

    assert pointer is not None, "the diff path must find a pointer the colour model misses"
    assert pointer.method == "diff"
    assert abs(pointer.x * 360 - (start[0] + nudge * gain)) < 25
    assert abs(pointer.y * 640 - start[1]) < 25


def test_travel_moves_on_the_belief_without_clicking():
    from artemis.reflex.mouse import SessionTapController

    control = FakeControl()
    session = PointerSession(control, frame_size=(200, 200), gain=(2.0, 2.0))
    session.connect()
    controller = SessionTapController(session, capture=FakeCapture([_png((9, 9, 9))]))

    remaining = controller.travel(100, 100)

    assert control.moves, "travel is dead reckoning"
    assert control.clicks == 0, "travel is not a tap"
    assert remaining <= 8.0, "the pointer belief lands on the target"


def test_a_click_that_changes_only_the_target_crop_is_verified():
    """Verification looks at the target first: a press highlight there is the click."""
    from io import BytesIO

    from PIL import Image, ImageDraw

    from artemis.reflex.mouse import SessionTapController
    from artemis.reflex.vision import Pointer

    def frame(highlight: bool) -> bytes:
        image = Image.new("RGB", (200, 200), (235, 235, 235))
        if highlight:
            ImageDraw.Draw(image).rectangle((90, 90, 110, 110), fill=(10, 120, 220))
        out = BytesIO()
        image.save(out, "PNG")
        return out.getvalue()

    class HighlightOnClick:
        def __init__(self):
            self.clicked = False

        def grab_latest(self):
            from types import SimpleNamespace

            return SimpleNamespace(image=frame(self.clicked))

    capture = HighlightOnClick()
    control = FakeControl()
    session = PointerSession(control, frame_size=(200, 200), gain=(2.0, 2.0))
    session.connect()

    class ClickPaints(  # the click itself is what repaints the target
        type(control)
    ):
        pass

    original_click = control.mouse_click

    def click():
        original_click()
        capture.clicked = True

    control.mouse_click = click
    controller = SessionTapController(
        session,
        capture=capture,
        detector=FakeDetector([Pointer(0.5, 0.5, 0.95, "fake")] * 5),
        settle_timeout_s=0.0,
    )

    result = controller.tap(100, 100)

    assert result.ok is True
    assert result.crop_change is not None and result.crop_change > 0.0


def test_the_loop_learns_a_gain_the_configuration_guessed_wrong():
    """iOS accelerates: 1.2 device px per HID unit here, 1.8 configured.

    A fixed gain stops the loop short of the target (measured live 2026-10-21: the tap
    landed left of the icon it aimed at), so the ratio is measured from the observations.
    """
    from io import BytesIO
    from types import SimpleNamespace

    from PIL import Image, ImageDraw

    from artemis.reflex.mouse import SessionTapController
    from artemis.reflex.vision import Pointer

    TRUE_GAIN = 1.2
    FRAME = (449, 974)

    class Accelerating:
        """The truth: our moves land TRUE_GAIN device px per unit, not the configured 1.8."""

        def __init__(self, gain=(1.8, 1.8)):
            self.position = [0.0, 0.0]
            self.clicks = 0
            self.hid = SimpleNamespace(
                mouse_home=lambda: None,
                mouse_move=self._move,
                mouse_click=self._click,
            )
            self.moves: list[tuple[int, int]] = []

        def _click(self):
            self.clicks += 1

        def _move(self, dx, dy):
            self.moves.append((dx, dy))
            self.position[0] += dx * TRUE_GAIN
            self.position[1] += dy * TRUE_GAIN

    class Camera:
        """A frame whose dot is exactly at the true pointer position."""

        def __init__(self, truth):
            self.truth = truth
            self.clicks = 0

        def grab_latest(self):
            image = Image.new("RGB", FRAME, (235, 235, 235))
            draw = ImageDraw.Draw(image)
            x = int(min(FRAME[0] - 6, max(6, self.truth.position[0])))
            y = int(min(FRAME[1] - 6, max(6, self.truth.position[1])))
            draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(230, 40, 40))
            buffer = BytesIO()
            image.save(buffer, "PNG")
            return SimpleNamespace(image=buffer.getvalue())

    class Detector:
        """Finds the red dot the camera draws, in whatever image it is handed.

        Image-aware on purpose: the observation corridor crops the frame, so a detector
        that returns absolute coordinates regardless of the image would mis-map the crop.
        """

        def detect(self, image, near=None):
            import numpy as np

            with Image.open(BytesIO(image)) as img:
                pixels = np.asarray(img.convert("RGB"))
            mask = (pixels[:, :, 0] > 180) & (pixels[:, :, 1] < 90) & (pixels[:, :, 2] < 90)
            ys, xs = np.nonzero(mask)
            if not len(xs):
                return None
            return Pointer(
                float(xs.mean()) / pixels.shape[1],
                float(ys.mean()) / pixels.shape[0],
                0.9,
                "fake-dot",
            )

    truth = Accelerating()
    session = PointerSession(truth.hid, frame_size=FRAME, gain=(1.8, 1.8))
    session.connected = True
    camera = Camera(truth)
    controller = SessionTapController(
        session,
        capture=camera,
        detector=Detector(),
        max_steps=8,
        max_step=50,
        settle_timeout_s=0.0,
    )

    target = (240.0, 600.0)
    controller.tap(target[0], target[1])

    reached = ((truth.position[0] - target[0]) ** 2 + (truth.position[1] - target[1]) ** 2) ** 0.5
    assert truth.clicks >= 1, "the loop clicked on a verified pointer"
    assert reached <= 8.0, f"the pointer truly reached the target (off by {reached:.1f})"
    assert controller._gain[0] < 1.8, "the measured ratio replaced the configured guess"


def test_travel_uses_the_online_gain_not_the_configured_default():
    from artemis.reflex.mouse import SessionTapController

    control = FakeControl()
    session = PointerSession(control, frame_size=(200, 200), gain=(2.0, 2.0))
    session.connect()
    controller = SessionTapController(session, capture=None)
    controller._gain = [4.0, 4.0]  # what observations have measured on this rig

    remaining = controller.travel(100, 100)

    assert control.moves == [(25, 25)], "100 px at the measured 4.0 px/unit, not the 2.0 default"
    assert remaining <= controller.tolerance_px


class _LinearPointer:
    """A control whose pointer moves by ``units * gain`` (the rig's model, no acceleration)."""

    def __init__(self, gain=2.0, start=(0.0, 0.0)):
        self.gain = gain
        self.x, self.y = start
        self.moves = []
        self.clicks = 0

    def preflight(self):
        return {"mounted": 1, "suspended": 0, "mouse": 1, "keyboard": 1, "touch": 0}

    def ios_home(self):
        pass

    def mouse_home(self):
        self.x = self.y = 0.0

    def mouse_move(self, dx, dy):
        self.moves.append((dx, dy))
        self.x += dx * self.gain
        self.y += dy * self.gain

    def mouse_click(self):
        self.clicks += 1


class _DotCamera:
    """Draws a red dot at the virtual pointer; after a click the screen repaints."""

    def __init__(self, control, frame_size=(449, 974)):
        self.control = control
        self.frame_size = frame_size

    def grab_latest(self):
        from io import BytesIO
        from types import SimpleNamespace

        from PIL import Image, ImageDraw

        base = (240, 240, 240) if self.control.clicks else (200, 200, 200)
        image = Image.new("RGB", self.frame_size, base)
        x = int(min(self.frame_size[0] - 8, max(8.0, self.control.x)))
        y = int(min(self.frame_size[1] - 8, max(8.0, self.control.y)))
        ImageDraw.Draw(image).ellipse((x - 6, y - 6, x + 6, y + 6), fill=(230, 40, 40))
        buffer = BytesIO()
        image.save(buffer, "PNG")
        return SimpleNamespace(image=buffer.getvalue())


class _DotDetector:
    """Finds the red dot in whatever image it is handed (full frame or corridor crop)."""

    def detect(self, image, near=None):
        from io import BytesIO

        import numpy as np
        from PIL import Image

        from artemis.reflex.vision import Pointer

        with Image.open(BytesIO(image)) as img:
            pixels = np.asarray(img.convert("RGB"))
        mask = (pixels[:, :, 0] > 180) & (pixels[:, :, 1] < 90) & (pixels[:, :, 2] < 90)
        ys, xs = np.nonzero(mask)
        if not len(xs):
            return None
        return Pointer(
            float(xs.mean()) / pixels.shape[1],
            float(ys.mean()) / pixels.shape[0],
            0.95,
            "fake-dot",
        )


def test_a_far_target_converges_in_phases_and_reports_them():
    from artemis.reflex.mouse import SessionTapController

    control = _LinearPointer(gain=2.0)
    session = PointerSession(control, frame_size=(449, 974), gain=(2.0, 2.0))
    session.connect()
    controller = SessionTapController(
        session,
        capture=_DotCamera(control),
        detector=_DotDetector(),
        max_step=20,  # a deliberately short first leg: the planner owns the travel
        settle_timeout_s=0.0,
    )

    result = controller.tap(400, 800)

    assert result.ok is True
    assert control.clicks == 1
    assert result.motion is not None
    assert result.motion.segments >= 1
    # The first leg is a planned segment too, so every segment is followed by exactly
    # one observation (the one that verifies it before the next plan).
    assert result.motion.observations == result.motion.segments
    assert "ballistic" in result.motion.phases


def test_a_corridor_miss_falls_back_to_the_full_frame_and_counts_a_recovery():
    from artemis.reflex.mouse import MotionReport, SessionTapController
    from artemis.reflex.vision import Pointer

    class FirstMissThenFound:
        """The ROI crop is a miss; the full frame finds the pointer."""

        def __init__(self):
            self.calls = 0

        def detect(self, image, near=None):
            self.calls += 1
            return None if self.calls == 1 else Pointer(0.5, 0.5, 0.9, "fake")

    control = FakeControl()
    session = PointerSession(control, frame_size=(449, 974), gain=(2.0, 2.0))
    session.connected = True
    capture = FakeCapture([_png((120, 120, 120))] * 6)
    controller = SessionTapController(
        session, capture=capture, detector=FirstMissThenFound(), settle_timeout_s=0.0
    )
    report = MotionReport()

    frame, pointer = controller._observe(corridor=((100.0, 100.0), (300.0, 600.0)), motion=report)

    assert pointer is not None, "the full-frame recovery still finds the pointer"
    assert report.global_recoveries == 1


def test_a_corridor_hit_does_not_count_a_recovery():
    from artemis.reflex.mouse import MotionReport, SessionTapController
    from artemis.reflex.vision import Pointer

    class AlwaysFound:
        def detect(self, image, near=None):
            return Pointer(0.5, 0.5, 0.9, "fake")

    control = FakeControl()
    session = PointerSession(control, frame_size=(449, 974), gain=(2.0, 2.0))
    session.connected = True
    capture = FakeCapture([_png((120, 120, 120))] * 6)
    controller = SessionTapController(
        session, capture=capture, detector=AlwaysFound(), settle_timeout_s=0.0
    )
    report = MotionReport()

    frame, pointer = controller._observe(corridor=((100.0, 100.0), (300.0, 600.0)), motion=report)

    assert pointer is not None
    assert report.global_recoveries == 0


def test_a_wake_move_is_not_billed_to_the_last_planned_segment():
    """The 60-unit wake must not be learned as part of the segment's gain."""
    from artemis.reflex.motion import Phase
    from artemis.reflex.mouse import SessionTapController
    from artemis.reflex.vision import Pointer

    class Scripted:
        """Position 1, two misses (corridor + full frame, so the wake fires), position 2."""

        def __init__(self, positions):
            self.positions = positions
            self.calls = 0

        def detect(self, image, near=None):
            index = min(self.calls, len(self.positions) - 1)
            self.calls += 1
            point = self.positions[index]
            if point is None:
                return None
            return Pointer(point[0] / 449, point[1] / 974, 0.95, "fake")

    control = _LinearPointer(gain=2.0)
    session = PointerSession(control, frame_size=(449, 974), gain=(2.0, 2.0))
    session.connect()
    controller = SessionTapController(
        session,
        capture=_DotCamera(control),
        # call 1: first observation; calls 2-3: the corridor crop and the full-frame
        # fallback both miss (so the wake fires); call 4: the post-wake observation.
        detector=Scripted([(320.0, 600.0), None, None, (500.0, 700.0)]),
        max_steps=3,
        max_attempts=1,
        observe_attempts=1,
        settle_timeout_s=0.0,
    )

    controller.tap(400, 800)

    # (500,700) - (320,600) includes the wake's 60 units; attributing it to the segment
    # would move the approach gain off the initial 2.0 (the review's Critical finding).
    assert controller.motion_calibration.gain(Phase.APPROACH) == (2.0, 2.0)


class _TimedCapture:
    """A capture whose frames carry a timestamp (the UVC frame's captured_at)."""

    def __init__(self, frames):
        self.frames = frames
        self.index = 0
        self.grabs = 0

    def grab_latest(self, timeout_s=None):
        from types import SimpleNamespace

        self.grabs += 1
        image, captured_at = self.frames[min(self.index, len(self.frames) - 1)]
        self.index += 1
        return SimpleNamespace(image=image, captured_at=captured_at)


def test_one_grab_per_observation_and_the_frame_must_postdate_the_move():
    from artemis.reflex.mouse import SessionTapController
    from artemis.reflex.vision import Pointer

    class Found:
        def detect(self, image, near=None):
            return Pointer(0.5, 0.5, 0.9, "fake")

    capture = _TimedCapture([(_png((120, 120, 120)), 100.0)] * 6)
    control = FakeControl()
    session = PointerSession(control, frame_size=(200, 200), gain=(2.0, 2.0))
    session.connected = True
    controller = SessionTapController(
        session, capture=capture, detector=Found(), settle_timeout_s=0.5
    )

    frame = controller._frame_after(b"an-older-frame", not_before=50.0)

    assert capture.grabs == 1, "one grab is already a live frame"
    assert frame.captured_at == 100.0

    stale = _TimedCapture([(_png((120, 120, 120)), 10.0), (_png((120, 120, 120)), 70.0)])
    controller.capture = stale

    frame = controller._frame_after(b"an-older-frame", not_before=50.0)

    assert stale.grabs == 2, "a frame captured before the move is skipped"
    assert frame.captured_at == 70.0


def test_a_detection_outside_the_corridor_is_counted_as_a_recovery():
    from artemis.reflex.mouse import MotionReport, SessionTapController
    from artemis.reflex.vision import Pointer

    class Outside:
        def detect(self, image, near=None):
            return Pointer(0.9, 0.9, 0.9, "fake")  # (404, 876): not on the corridor

    control = FakeControl()
    session = PointerSession(control, frame_size=(449, 974), gain=(2.0, 2.0))
    session.connected = True
    capture = FakeCapture([_png((120, 120, 120))] * 6)
    controller = SessionTapController(
        session, capture=capture, detector=Outside(), settle_timeout_s=0.0
    )
    report = MotionReport()

    frame, pointer = controller._observe(corridor=((100.0, 100.0), (300.0, 600.0)), motion=report)

    assert pointer is not None, "the full frame still finds the pointer"
    assert report.global_recoveries == 1


def test_the_first_leg_is_calibrated_and_the_belief_uses_the_phase_gain():
    """The first leg's displacement must teach the ballistic phase, and the belief that
    predicts the next position must integrate on the same phase gain as the plan."""
    from artemis.reflex.motion import Phase
    from artemis.reflex.mouse import SessionTapController

    control = _LinearPointer(gain=3.0)  # the rig is not the configured 2.0
    session = PointerSession(control, frame_size=(449, 974), gain=(2.0, 2.0))
    session.connect()
    controller = SessionTapController(
        session,
        capture=_DotCamera(control),
        detector=_DotDetector(),
        max_steps=1,
        max_attempts=1,
        settle_timeout_s=0.0,
    )

    result = controller.tap(200, 400)

    # First leg: (80,160) units at 3.0 px/unit = (240,480) observed.
    assert controller.motion_calibration.gain(Phase.BALLISTIC) == pytest.approx((2.4, 2.4))
    # The last segment planned was fine, and the belief integrated on that phase's gain.
    last_phase = Phase(result.motion.phases[-1])
    assert session.gain_x == pytest.approx(controller.motion_calibration.gain(last_phase)[0])
    assert session.gain_y == pytest.approx(controller.motion_calibration.gain(last_phase)[1])


def test_a_tiny_segment_does_not_teach_the_phase_gain():
    """A few units are dominated by rounding; calibrating on them drifts the gain."""
    from artemis.reflex.motion import Phase
    from artemis.reflex.mouse import SessionTapController

    control = _LinearPointer(gain=3.0)
    session = PointerSession(control, frame_size=(449, 974), gain=(2.0, 2.0))
    session.connect()
    controller = SessionTapController(
        session,
        capture=_DotCamera(control),
        detector=_DotDetector(),
        max_steps=2,
        max_attempts=1,
        settle_timeout_s=0.0,
    )
    # At the target the remaining error is one or two units per axis: below the guard.
    result = controller.tap(2, 4)

    assert result.motion.segments >= 1
    assert controller.motion_calibration.gain(Phase.BALLISTIC) == (2.0, 2.0)


class _AbsolutePointer(_LinearPointer):
    """A control with the absolute report; moves land at the requested frame point."""

    def __init__(self, gain=2.0, *, honor_absolute=True):
        super().__init__(gain=gain)
        self.honor_absolute = honor_absolute
        self.absolute_moves = []
        self.absolute_clicks = 0

    def mouse_move_abs(self, nx, ny):
        self.absolute_moves.append((nx, ny))
        if self.honor_absolute:
            self.x = nx / 1000 * 449
            self.y = ny / 1000 * 974

    def mouse_click_abs(self):
        self.absolute_clicks += 1
        self.clicks += 1  # the camera repaints when a click lands


def test_an_absolute_tap_is_one_move_and_one_click():
    from artemis.reflex.mouse import SessionTapController

    control = _AbsolutePointer()
    session = PointerSession(control, frame_size=(449, 974), gain=(2.0, 2.0))
    session.connect()
    controller = SessionTapController(
        session, capture=_DotCamera(control), detector=_DotDetector(), settle_timeout_s=0.0
    )

    result = controller.tap(224, 487)

    assert result.ok is True
    assert control.absolute_moves == [(499, 500)], "one normalized absolute move"
    assert control.absolute_clicks == 1
    assert result.motion.phases[0] == "absolute"
    assert result.motion.segments == 1


def test_an_ignored_absolute_move_falls_back_to_relative_segments():
    from artemis.reflex.mouse import SessionTapController

    control = _AbsolutePointer(honor_absolute=False)
    session = PointerSession(control, frame_size=(449, 974), gain=(2.0, 2.0))
    session.connect()
    controller = SessionTapController(
        session, capture=_DotCamera(control), detector=_DotDetector(), settle_timeout_s=0.0
    )

    result = controller.tap(224, 487)

    assert result.ok is True
    assert controller._absolute_supported is False
    assert control.absolute_clicks == 0, "the relative click is the one that runs"
    assert result.motion.segments >= 2
