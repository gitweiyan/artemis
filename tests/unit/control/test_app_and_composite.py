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

"""Absolute control surfaces and the capability-ordered composition of them."""

from __future__ import annotations

import asyncio

import pytest

from artemis.control.app_control import AppControlSink
from artemis.control.base import ActionResult
from artemis.control.composite import CompositeControlSink
from artemis.devices.capabilities import CapabilitySet


class FakeDriver:
    def __init__(self, *, tap_ok=True, fail=None):
        self.tap_ok = tap_ok
        self.fail = fail
        self.calls: list = []

    async def tap(self, x, y):
        self.calls.append(("tap", x, y))
        if self.fail == "tap":
            raise RuntimeError("adb went away")
        return self.tap_ok

    async def swipe(self, x1, y1, x2, y2, duration_ms=800):
        self.calls.append(("swipe", x1, y1, x2, y2))
        return True

    async def input_text(self, text):
        self.calls.append(("text", text))
        return True

    async def press_key(self, key):
        self.calls.append(("key", key))
        return True


class FakeRelative:
    capabilities = CapabilitySet(supports_relative_pointer=True, requires_pointer_precheck=True)

    def __init__(self):
        self.calls: list = []

    async def tap_abs(self, x, y):
        self.calls.append(("tap_abs", x, y))
        return ActionResult(True, "tap_abs")

    async def tap_rel(self, dx, dy):
        self.calls.append(("tap_rel", dx, dy))
        return ActionResult(True, "tap_rel")

    async def swipe(self, *args, **kwargs):
        self.calls.append(("swipe", args))
        return ActionResult(True, "swipe")

    async def input_text(self, text):
        return ActionResult(True, "input_text")

    async def press_key(self, key):
        return ActionResult(True, "press_key")


def test_the_app_sink_is_absolute_and_says_it_cannot_move_relatively():
    sink = AppControlSink(FakeDriver())

    assert sink.capabilities.supports_absolute_tap is True
    assert sink.capabilities.supports_relative_pointer is False
    assert asyncio.run(sink.tap_rel(5, 5)).incident == "unsupported"


def test_the_app_sink_reports_device_refusals_as_incidents():
    sink = AppControlSink(FakeDriver(tap_ok=False))

    result = asyncio.run(sink.tap_abs(10, 20))

    assert result.ok is False
    assert result.incident == "tap_failed"


def test_the_app_sink_turns_a_raise_into_an_incident():
    sink = AppControlSink(FakeDriver(fail="tap"))

    result = asyncio.run(sink.tap_abs(10, 20))

    assert result.ok is False
    assert "adb went away" in result.detail


def test_text_and_keys_go_through_the_driver():
    driver = FakeDriver()
    sink = AppControlSink(driver)

    asyncio.run(sink.input_text("hello"))
    asyncio.run(sink.press_key("enter"))

    assert ("text", "hello") in driver.calls
    assert ("key", "enter") in driver.calls


def test_the_composite_prefers_absolute_control_when_it_exists():
    absolute = AppControlSink(FakeDriver())
    relative = FakeRelative()
    sink = CompositeControlSink([relative, absolute])

    result = asyncio.run(sink.tap_abs(100, 200))

    assert result.ok is True
    assert relative.calls == [], "the relative pointer is only a fallback"
    assert sink.capabilities.supports_absolute_tap is True


def test_the_composite_falls_back_to_the_relative_pointer():
    relative = FakeRelative()
    sink = CompositeControlSink([relative])

    asyncio.run(sink.tap_abs(100, 200))
    asyncio.run(sink.tap_rel(5, -5))

    assert ("tap_abs", 100, 200) in relative.calls
    assert ("tap_rel", 5, -5) in relative.calls


def test_a_failed_action_is_not_retried_through_another_surface():
    """Retrying after a partial action would act twice."""

    class Refusing(AppControlSink):
        async def tap_abs(self, x, y):
            return ActionResult(False, "tap_abs", "no_effect", "nothing changed")

    relative = FakeRelative()
    sink = CompositeControlSink([Refusing(FakeDriver()), relative])

    result = asyncio.run(sink.tap_abs(100, 200))

    assert result.ok is False
    assert result.incident == "no_effect"
    assert relative.calls == []


def test_no_surface_can_express_an_action_that_none_supports():
    bare = FakeRelative()
    bare.capabilities = CapabilitySet(supports_relative_pointer=True)
    sink = CompositeControlSink([bare])

    result = asyncio.run(sink.input_text("x"))

    assert result.ok is False
    assert result.incident == "unsupported"
    assert "text input" in result.detail


def test_a_composite_needs_at_least_one_surface():
    with pytest.raises(ValueError):
        CompositeControlSink([])
