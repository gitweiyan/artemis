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

"""Calibration measures the gain the rig actually has, including its acceleration."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from artemis.drivers.ios.calibration import (
    GainSample,
    calibrate_pointer,
    fit_gain,
)
from artemis.reflex.mouse import PointerSession, SessionTapController
from artemis.reflex.vision import Pointer

FRAME = (449, 974)


def test_fit_gain_is_the_median_ratio_so_one_bad_reading_does_not_poison_it():
    """The unit is device pixels per HID unit, not pixels."""
    samples = [
        GainSample(25, 25, 30.0, 30.0),  # 1.20
        GainSample(25, 25, 31.0, 29.0),  # 1.24 / 1.16
        GainSample(25, 25, 300.0, 12.0),  # 12.0 — a mis-detection, dropped
    ]

    assert fit_gain(samples, "x") == pytest.approx(1.22)
    assert fit_gain(samples, "y") == pytest.approx(1.16)
    assert fit_gain([], "x") is None


def test_a_tiny_axis_is_not_used_for_gain():
    assert GainSample(2, 2, 10.0, 10.0).gain("x") is None


class Truth:
    """A pointer whose gain depends on the move size: iOS accelerates."""

    SMALL_GAIN = 1.2
    LARGE_GAIN = 2.0

    def __init__(self):
        self.position = [10.0, 10.0]
        self.hid = SimpleNamespace(
            mouse_home=self._home, mouse_move=self._move, mouse_click=lambda: None
        )

    def _home(self):
        self.position = [0.0, 0.0]

    def _move(self, dx, dy):
        gain = self.SMALL_GAIN if max(abs(dx), abs(dy)) <= 40 else self.LARGE_GAIN
        self.position[0] = min(FRAME[0], max(0.0, self.position[0] + dx * gain))
        self.position[1] = min(FRAME[1], max(0.0, self.position[1] + dy * gain))


class Camera:
    def __init__(self, truth):
        self.truth = truth

    def grab_latest(self):
        return SimpleNamespace(image=b"frame")


class Detector:
    def __init__(self, truth):
        self.truth = truth

    def detect(self, image, near=None):
        return Pointer(
            min(FRAME[0], self.truth.position[0]) / FRAME[0],
            min(FRAME[1], self.truth.position[1]) / FRAME[1],
            0.9,
            "fake",
        )


def _controller(truth, detector=None):
    session = PointerSession(truth.hid, frame_size=FRAME, gain=(1.8, 1.8))
    session.connected = True
    return SessionTapController(
        session,
        capture=Camera(truth),
        detector=detector or Detector(truth),
        settle_timeout_s=0.0,
    )


def test_the_two_bands_are_measured_and_a_starting_value_is_suggested():
    truth = Truth()
    result = asyncio.run(calibrate_pointer(_controller(truth)))

    assert result.small_gain is not None and abs(result.small_gain - 1.2) < 0.05
    assert result.large_gain is not None and abs(result.large_gain - 2.0) < 0.05
    assert result.acceleration_ratio is not None and result.acceleration_ratio > 1.3
    assert result.suggested is not None
    assert result.suggested[0] < 1.8, "the configuration's guess is replaced by a measurement"
    assert "accelerates" in " ".join(result.notes)


def test_the_result_prints_an_env_line_for_the_rig():
    result = asyncio.run(calibrate_pointer(_controller(Truth())))

    assert result.env_line().startswith("ARTEMIS_IOS_POINTER_GAIN=")
    assert "suggested=" in result.describe()


def test_an_invisible_pointer_fails_with_a_reason():
    class Blind:
        def detect(self, image, near=None):
            return None

    result = asyncio.run(calibrate_pointer(_controller(Truth(), detector=Blind())))

    assert result.suggested is None
    assert "not visible" in " ".join(result.notes)
    assert "failed" in result.describe()


def test_a_controller_without_a_camera_cannot_calibrate():
    truth = Truth()
    session = PointerSession(truth.hid, frame_size=FRAME)
    controller = SessionTapController(session, capture=None)

    result = asyncio.run(calibrate_pointer(controller))

    assert result.suggested is None
    assert "no capture" in " ".join(result.notes)
