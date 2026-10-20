# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Skills speak absolute gestures; an iPhone only accepts a relative pointer.

The adapter is the seam that lets a deterministic skill (Tetris and friends) run on the
iOS rig at all, without teaching every skill about dead reckoning, prechecks or frames.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from artemis.devices.geometry import ScreenGeometry
from artemis.reflex.contract import Gesture
from artemis.reflex.pointer_control import PointerControl


class FakeTapController:
    """Stands in for SessionTapController: records calls, returns scripted results."""

    def __init__(self, *, ok=True, incident=None, message="ok"):
        self.ok = ok
        self.incident = incident
        self.message = message
        self.taps = []
        self.travels = []
        self.remaining = 0.0

    def tap(self, x, y):
        self.taps.append((x, y))
        return SimpleNamespace(
            ok=self.ok, incident=self.incident, message=self.message, attempts=1
        )

    def travel(self, x, y):
        self.travels.append((x, y))
        return self.remaining


class FakeHid:
    def __init__(self):
        self.events = []

    def mouse_down(self):
        self.events.append("down")

    def mouse_move(self, dx, dy):
        self.events.append((dx, dy))

    def mouse_up(self):
        self.events.append("up")

    def preflight(self):
        return {"mounted": 1}


class FakePointerSession:
    def __init__(self):
        self.control = FakeHid()
        self.position = (0.0, 0.0)
        self.gain_x = 1.8
        self.gain_y = 1.8

    def move(self, dx, dy):
        self.position = (self.position[0] + dx * self.gain_x, self.position[1] + dy * self.gain_y)
        self.control.mouse_move(dx, dy)
        return self.position


def _control(controller=None, *, geometry=None, session=None):
    controller = controller or FakeTapController()
    session = session or FakePointerSession()
    return (
        PointerControl(
            controller,
            session=session,
            geometry=geometry or ScreenGeometry((449, 974), (1080, 2400)),
        ),
        controller,
        session,
    )


def test_a_tap_is_converted_from_device_pixels_to_capture_frame_pixels():
    control, controller, _session = _control()

    latency = control.dispatch(Gesture.tap(540, 1200, description="play"))

    assert controller.taps == [(224, 487)], "the controller speaks frame pixels"
    assert latency >= 0.0


def test_a_refused_tap_raises_so_the_runtime_can_stop():
    controller = FakeTapController(
        ok=False, incident="pointer_missing", message="pointer not detected before click"
    )
    control, _c, _session = _control(controller)

    with pytest.raises(RuntimeError) as excinfo:
        control.dispatch(Gesture.tap(500, 500))

    assert "pointer_missing" in str(excinfo.value)
    assert "refused" in str(excinfo.value).lower(), "the runtime counts failures, so say it"


def test_a_swipe_presses_moves_and_releases():
    control, controller, session = _control()
    controller.remaining = 0.0

    control.dispatch(
        Gesture.swipe(540, 1800, 540, 400, duration_ms=200, description="drag up")
    )

    assert controller.travels, "the pointer is positioned before the drag"
    assert session.control.events[0] == "down"
    assert session.control.events[-1] == "up"
    moves = [event for event in session.control.events if isinstance(event, tuple)]
    assert moves, "the drag is expressed as relative moves"
    for dx, dy in moves:
        assert abs(dx) <= 127 and abs(dy) <= 127, "int8 segments, like every other move"


def test_a_pointer_that_cannot_reach_the_drag_start_is_refused():
    controller = FakeTapController()
    controller.remaining = 120.0  # the pointer never arrived
    control, _c, session = _control(controller)

    with pytest.raises(RuntimeError) as excinfo:
        control.dispatch(Gesture.swipe(900, 2000, 900, 600))

    assert "pointer" in str(excinfo.value).lower()
    assert session.control.events == [], "no button is pressed from the wrong place"


def test_an_unknown_gesture_is_rejected():
    control, _controller, _session = _control()

    with pytest.raises(ValueError):
        control.dispatch(Gesture(kind="pinch", points=((1.0, 1.0), (2.0, 2.0))))
