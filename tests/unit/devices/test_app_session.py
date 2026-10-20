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

"""The app composition is a DeviceSession like any other, built from a driver."""

from __future__ import annotations

import asyncio
from io import BytesIO
from types import SimpleNamespace

from PIL import Image
import pytest

from artemis.devices.app_session import build_app_session


def _png() -> bytes:
    out = BytesIO()
    Image.new("RGB", (16, 16), (200, 200, 200)).save(out, "PNG")
    return out.getvalue()


class Driver:
    screen_size = (1080, 2400)

    def __init__(self):
        self.taps = []
        self._data = SimpleNamespace(
            screenshot_bytes=_png(),
            ui_elements=[{"text": "设置", "bounds": [0, 0, 10, 10]}],
            width=1080,
            height=2400,
            timestamp=1.0,
        )

    async def get_screen_data(self):
        return self._data

    async def tap(self, x, y):
        self.taps.append((x, y))
        return True

    async def swipe(self, *args, **kwargs):
        return True

    async def input_text(self, text):
        return True

    async def press_key(self, key):
        return True


def test_the_session_exposes_a_tree_and_absolute_control():
    driver = Driver()
    session = build_app_session(driver)

    assert session.capabilities.supports_ui_tree is True
    assert session.capabilities.supports_absolute_tap is True
    assert session.capabilities.requires_pointer_precheck is False
    assert session.control_for(absolute=True).driver is driver
    assert asyncio.run(session.perception[0].ui_tree())[0].text == "设置"


def test_a_healthy_frame_comes_back_and_a_screenshot_is_byte_exact():
    session = build_app_session(Driver())

    frame = asyncio.run(session.snapshot())

    assert frame.health == "ok"
    assert (frame.width, frame.height) == (1080, 2400)


def test_geometry_uses_the_driver_screen_size_by_default():
    driver = Driver()
    driver.screen_size = (1170, 2532)

    session = build_app_session(driver)

    assert session.geometry.screen_size == (1170, 2532)


def test_an_explicit_screen_size_wins():
    session = build_app_session(Driver(), screen_size=(1000, 2000))

    assert session.geometry.screen_size == (1000, 2000)


def test_taps_reach_the_driver_in_device_pixels():
    driver = Driver()
    session = build_app_session(driver)

    result = asyncio.run(session.control_for(absolute=True).tap_abs(540, 1200))

    assert result.ok is True
    assert driver.taps == [(540, 1200)]


def test_the_geometry_rejects_an_impossible_content_rect():
    with pytest.raises(ValueError):
        build_app_session(Driver(), content_rect=(0, 0, 2000, 100))
