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
    def __init__(self, frames: list[bytes]):
        self.frames = frames
        self.index = 0

    def grab_latest(self, timeout_s=None):
        from types import SimpleNamespace

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
    controller = SessionTapController(session, capture=capture)

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
    controller = SessionTapController(session, capture=capture, max_attempts=2)

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
    capture = FakeCapture([_png((120, 120, 120)), _png((240, 40, 40))])
    detector = FakeDetector([Pointer(0.5, 0.5, 0.95, "fake")])
    controller = SessionTapController(session, capture=capture, detector=detector)

    result = controller.tap(100, 100)

    assert result.ok is True
    assert control.clicks == 1
    assert control.moves == []


def test_verified_tap_corrects_an_offset_pointer_before_clicking():
    from artemis.reflex.mouse import SessionTapController
    from artemis.reflex.vision import Pointer

    control = FakeControl()
    session = PointerSession(control, frame_size=(200, 200), gain=(2.0, 2.0))
    session.connect()
    capture = FakeCapture([_png((120, 120, 120)), _png((120, 120, 120)), _png((240, 40, 40))])
    detector = FakeDetector([Pointer(0.25, 0.25, 0.95, "fake"), Pointer(0.5, 0.5, 0.95, "fake")])
    controller = SessionTapController(session, capture=capture, detector=detector)

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
    controller = SessionTapController(session, capture=capture, detector=detector)

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
    controller = SessionTapController(session, capture=capture, detector=detector)

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
    controller = SessionTapController(session, capture=capture, detector=detector)

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
    controller = SessionTapController(session, capture=capture, detector=detector)

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
        session, capture=capture, detector=detector, max_attempts=1, max_steps=2
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
    capture = FakeCapture([_png((120, 120, 120)), _png((120, 120, 120)), _png((240, 40, 40))])
    detector = FakeDetector([None, Pointer(0.5, 0.5, 0.95, "fake")])
    controller = SessionTapController(session, capture=capture, detector=detector)

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
        session, capture=capture, detector=detector, observe_attempts=3
    )

    result = controller.tap(100, 100)

    assert detector.calls == 3
    assert result.incident == "pointer_missing"
    assert control.clicks == 0
