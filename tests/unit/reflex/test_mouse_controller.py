"""P2: Mouse Controller v2 — turn a target into relative moves + click + verify.

The controller never integrates dx/dy as truth: it re-observes the pointer after
each move, rejects low-confidence readings, and only reports success when the
click visibly changed the screen.
"""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from artemis.reflex.mouse import MouseController
from artemis.reflex.vision import Pointer


def _png(color: tuple[int, int, int]) -> bytes:
    out = BytesIO()
    Image.new("RGB", (100, 100), color).save(out, "PNG")
    return out.getvalue()


class FakeCapture:
    def __init__(self, frames: list[bytes]):
        self.frames = frames
        self.index = 0

    def grab_latest(self, timeout_s=None):
        frame = self.frames[min(self.index, len(self.frames) - 1)]
        self.index += 1
        return SimpleNamespace(image=frame)


class FakeTracker:
    has_template = False

    def __init__(self, points: list[Pointer | None]):
        self.points = list(points)

    def detect(self, frame):
        return None

    def locate(self, before, after, near, shift, reach=(0.12, 0.08)):
        return self.points.pop(0) if self.points else None


class FakeControl:
    def __init__(self):
        self.moves: list[tuple[int, int]] = []
        self.clicks = 0
        self.homes = 0
        self.preflights = 0

    def preflight(self):
        self.preflights += 1

    def mouse_move(self, dx, dy):
        self.moves.append((dx, dy))

    def mouse_click(self):
        self.clicks += 1

    def mouse_home(self):
        self.homes += 1


def test_a_far_pointer_is_moved_then_clicked_and_verified():
    before = _png((120, 120, 120))
    after = _png((240, 40, 40))
    capture = FakeCapture([before, before, before, before, before, after])
    tracker = FakeTracker([Pointer(0.30, 0.50, 0.9, "diff"), Pointer(0.50, 0.50, 0.9, "diff")])
    control = FakeControl()
    controller = MouseController(
        control,
        capture,
        tracker,
        frame_size=(100, 100),
        gain=1.0,
        tolerance_px=5,
        nudge=10,
    )

    result = controller.tap(0.50, 0.50)

    assert result.ok is True
    assert result.attempts == 2
    assert control.preflights == 1
    assert control.clicks == 1
    assert (10, 0) in control.moves and (-10, 0) in control.moves
    assert (20, 0) in control.moves, "30% -> 50% of 100 px is a 20 px coarse move"


def test_low_confidence_pointer_is_not_clicked():
    frame = _png((120, 120, 120))
    capture = FakeCapture([frame] * 8)
    tracker = FakeTracker([Pointer(0.50, 0.50, 0.1, "diff")] * 4)
    control = FakeControl()
    controller = MouseController(control, capture, tracker, frame_size=(100, 100), max_attempts=2)

    result = controller.tap(0.50, 0.50)

    assert result.ok is False
    assert control.clicks == 0
    assert control.homes >= 1


def test_a_click_that_changes_nothing_is_retried_and_reported_as_failed():
    frame = _png((120, 120, 120))
    capture = FakeCapture([frame] * 20)
    tracker = FakeTracker([Pointer(0.50, 0.50, 0.9, "diff")] * 6)
    control = FakeControl()
    controller = MouseController(
        control, capture, tracker, frame_size=(100, 100), max_attempts=2, tolerance_px=5
    )

    result = controller.tap(0.50, 0.50)

    assert result.ok is False
    assert control.clicks == 2, "each attempt clicks, but the screen never changes"
    assert result.attempts == 2


def test_a_pointer_that_is_never_found_fails_without_clicking():
    frame = _png((120, 120, 120))
    capture = FakeCapture([frame] * 12)
    tracker = FakeTracker([None, None, None])
    control = FakeControl()
    controller = MouseController(control, capture, tracker, frame_size=(100, 100), max_attempts=2)

    result = controller.tap(0.50, 0.50)

    assert result.ok is False
    assert control.clicks == 0
    assert control.homes >= 1


def test_out_of_range_target_is_rejected():
    controller = MouseController(
        FakeControl(), FakeCapture([_png((0, 0, 0))]), FakeTracker([]), frame_size=(100, 100)
    )

    with pytest.raises(ValueError):
        controller.tap(1.2, 0.5)
