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

"""Actuator layer tests: coordinate conversion, message parity, driver dispatch."""

import pytest

from artemis.mcp.action_manifest import DEVICE_ACTIONS
from artemis.mcp.action_types import ActionCode
from artemis.mcp.actuators import Actuator, AdbActuator, MockActuator


@pytest.fixture
def actuator() -> MockActuator:
    return MockActuator(width=1000, height=2000)


def test_mock_actuator_satisfies_protocol(actuator):
    assert isinstance(actuator, Actuator)
    assert isinstance(actuator, AdbActuator)
    assert actuator.capabilities() == DEVICE_ACTIONS
    assert actuator.extensions() == []


@pytest.mark.asyncio
async def test_click_converts_normalized_to_pixels(actuator):
    res = await actuator.click(500, 500)
    assert res.ok and res.code is ActionCode.OK
    # Historical wording is part of the contract: transcripts and traces assert on it.
    assert res.message == "Tapped at [500, 500] (normalized)."
    assert res.normalized_coordinates == [500, 500]
    assert actuator.action_history[-1] == {
        "action": "tap",
        "x": 500,
        "y": 1000,
        "times": 1,
        "duration_ms": 100,
    }


@pytest.mark.asyncio
async def test_click_sequence_taps_each_point(actuator):
    res = await actuator.click_sequence([(100, 100), (900, 900)], delay_ms=1)
    assert res.ok
    assert res.message == ("Tapped in sequence at [100, 100]; [900, 900] (normalized).")
    taps = [h for h in actuator.action_history if h["action"] == "tap"]
    assert [(t["x"], t["y"]) for t in taps] == [(100, 200), (900, 1800)]


@pytest.mark.asyncio
async def test_long_press_message_and_duration(actuator):
    res = await actuator.long_press(250, 750, duration_ms=1500)
    assert res.ok
    assert res.message == "Long-pressed at [250, 750] (normalized) for 1500ms."
    assert res.duration_ms == 1500


@pytest.mark.asyncio
async def test_swipe_reports_normalized_endpoints(actuator):
    res = await actuator.swipe((500, 800), (500, 200), 400)
    assert res.ok
    assert res.message == "Swiped from [500, 800] to [500, 200] (normalized)."
    assert res.normalized_coordinates == [500, 800, 500, 200]


@pytest.mark.asyncio
async def test_press_key_forwards_unknown_keycode_to_driver(actuator):
    # Arbitrary keycodes (KEYCODE_* names, numeric codes) pass through to the driver,
    # matching the historical adb_server behavior of accepting any Android key event.
    res = await actuator.press_key("KEYCODE_DPAD_DOWN")
    assert res.ok
    assert res.message == "Pressed key 'KEYCODE_DPAD_DOWN'."
    assert actuator.action_history[-1]["action"] == "press_key"


@pytest.mark.asyncio
async def test_press_key_rejects_empty_key(actuator):
    res = await actuator.press_key("")
    assert not res.ok
    assert res.code is ActionCode.INVALID_ARGS


@pytest.mark.asyncio
async def test_input_text_append_moves_cursor_and_preserves_text(actuator, monkeypatch):
    monkeypatch.setattr("asyncio.sleep", _instant_sleep)
    res = await actuator.input_text("hello", (500, 300), clear_exist=False)
    assert res.ok
    assert res.message == "Typed 'hello' at [500, 300] (normalized)."
    actions = [h["action"] for h in actuator.action_history]
    # Append semantics: focus tap, cursor-to-end (KEYCODE_MOVE_END), then type without
    # clearing — matching the historical adb_server focus_and_input_text contract.
    assert actions == ["tap", "press_key", "input_text"]
    typed = actuator.action_history[-1]
    assert typed["clear_existing"] is False
    cursor = actuator.action_history[-2]
    assert cursor["key"] == "123"


@pytest.mark.asyncio
async def test_input_text_clear_erases_then_types_without_reclearing(actuator, monkeypatch):
    monkeypatch.setattr("asyncio.sleep", _instant_sleep)
    res = await actuator.input_text("hello", (500, 300), clear_exist=True)
    assert res.ok
    actions = [h["action"] for h in actuator.action_history]
    # The controller's full clear runs as a shell keyevent batch on this driver.
    assert actions == ["tap", "execute_shell", "input_text"]
    assert actuator.action_history[-1]["clear_existing"] is False


async def _instant_sleep(_delay, *args, **kwargs):
    return None


@pytest.mark.asyncio
async def test_wait_for_delay(actuator):
    res = await actuator.wait_for_delay(1)
    assert res.ok
    assert res.message == "Waited 1ms."


def test_partial_capabilities_are_reported():
    partial = MockActuator(capabilities=frozenset({"click_sequence"}))
    assert partial.capabilities() == frozenset({"click_sequence"})


@pytest.mark.asyncio
async def test_observation_primitives(actuator):
    shot = await actuator.take_screenshot()
    assert isinstance(shot, str) and len(shot) > 0
    data = await actuator.get_screen_data()
    assert data.width == 1000 and data.height == 2000


class _StructuredTapDriver:
    """A driver that reports why a tap failed (the iOS UVC + HID contract)."""

    def __init__(self, result):
        self.result = result
        self.calls = []

    async def tap(self, x, y, duration_ms=100, times=1, delay_ms=100):
        self.calls.append((x, y, "tap"))
        return self.result.ok

    async def tap_with_result(self, x, y):
        self.calls.append((x, y, "tap_with_result"))
        return self.result

    async def long_press(self, x, y, duration_ms=1000):
        self.calls.append((x, y, "long_press"))
        return self.result.ok


@pytest.mark.asyncio
async def test_tap_at_uses_the_structured_driver_result():
    from types import SimpleNamespace

    from artemis.control.base import ActionResult as DriverActionResult
    from third_party.mobile_use.controllers.unified_controller import UnifiedMobileControllerBase

    driver = _StructuredTapDriver(
        DriverActionResult(False, "tap_abs", "pointer_missing", "no pointer in frame")
    )
    controller = UnifiedMobileControllerBase(SimpleNamespace(), driver)

    out = await controller.tap_at(10, 20)

    assert out.error
    assert out.incident == "pointer_missing"
    assert driver.calls == [(10, 20, "tap_with_result")]


@pytest.mark.asyncio
async def test_tap_at_still_uses_plain_tap_when_no_structured_result_exists():
    from types import SimpleNamespace

    from third_party.mobile_use.controllers.unified_controller import UnifiedMobileControllerBase

    class _PlainDriver:
        def __init__(self):
            self.calls = []

        async def tap(self, x, y, duration_ms=100, times=1, delay_ms=100):
            self.calls.append((x, y))
            return True

    driver = _PlainDriver()
    controller = UnifiedMobileControllerBase(SimpleNamespace(), driver)

    out = await controller.tap_at(10, 20)

    assert out.error is None
    assert out.incident is None
    assert driver.calls == [(10, 20)]


class _ControllerWithIncident:
    def __init__(self, incident):
        from third_party.mobile_use.controllers.types import TapOutput

        self.tap_output = TapOutput(error="Tap failed at (540, 1200)", incident=incident)
        self.driver = None

    async def tap_at(self, x, y, **kwargs):
        return self.tap_output


@pytest.mark.asyncio
async def test_click_carries_the_driver_incident():
    from types import SimpleNamespace

    from artemis.mcp.actuators.adb import AdbActuator

    ctx = SimpleNamespace(device=SimpleNamespace(device_width=1080, device_height=2400))
    actuator = AdbActuator(ctx, controller=_ControllerWithIncident("pointer_missing"))

    res = await actuator.click(500, 500)

    assert res.ok is False
    assert res.incident == "pointer_missing"
    # The historical message is part of the contract; the incident rides alongside.
    assert res.message == "Error executing click: Tap failed at (540, 1200)"


@pytest.mark.asyncio
async def test_click_without_a_driver_incident_stays_unchanged():
    from types import SimpleNamespace

    from artemis.mcp.actuators.adb import AdbActuator

    ctx = SimpleNamespace(device=SimpleNamespace(device_width=1080, device_height=2400))
    actuator = AdbActuator(ctx, controller=_ControllerWithIncident(None))

    res = await actuator.click(500, 500)

    assert res.ok is False
    assert res.incident is None


@pytest.mark.asyncio
async def test_manage_app_is_refused_on_a_device_without_app_launch():
    """iOS has no package list: naming an app can never work, so say so immediately."""
    from types import SimpleNamespace

    from artemis.devices.capabilities import CapabilitySet
    from artemis.mcp.actuators.adb import AdbActuator

    ctx = SimpleNamespace(device=SimpleNamespace(device_width=1080, device_height=2400))
    controller = SimpleNamespace(
        driver=SimpleNamespace(capabilities=CapabilitySet(supports_app_launch=False))
    )

    res = await AdbActuator(ctx, controller=controller).manage_app("launch", "设置")

    assert res.ok is False
    assert res.code is ActionCode.UNSUPPORTED
    assert "Tap the app icon" in res.message
