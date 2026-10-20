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

"""The iOS chain, end to end, without hardware.

MCP action -> actuator -> unified controller -> IOSUvcHidDriver -> HidControlSink ->
SessionTapController -> (fake) UVC frames + (fake) ESP32 HID.

The point of these tests is the property the plan promises: a relative-pointer tap is
verified against a live frame before it clicks, and the reason a tap did not happen
survives all the way back to the MCP caller as a structured incident.
"""

import asyncio
from io import BytesIO
from types import SimpleNamespace

from PIL import Image
import pytest

from artemis.control.base import ActionResult as DriverActionResult
from artemis.devices.geometry import ScreenGeometry
from artemis.devices.ios_uvc import build_ios_uvc_session
from artemis.drivers.ios.ios_uvc_hid_driver import IOSUvcHidDriver
from artemis.mcp.action_server import build_action_server
from artemis.mcp.action_session import ActionSession
from artemis.mcp.actuators.adb import AdbActuator


def _frame(color=(120, 120, 120)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (200, 200), color).save(buffer, "PNG")
    return buffer.getvalue()


class FakeCapture:
    """A UVC card that returns the same phone picture every grab."""

    def __init__(self):
        self.grabs = 0

    def grab(self):
        self.grabs += 1
        return SimpleNamespace(image=_frame(), width=200, height=200, captured_at=1.0)


class FakeEsp32:
    """The ESP32-S3 HID board: records what the host asked it to do."""

    def __init__(self):
        self.clicks = 0
        self.moves = []
        self.homes = 0
        self.typed = []

    def preflight(self):
        return {"mounted": 1}

    def mouse_home(self):
        self.homes += 1

    def mouse_move(self, dx, dy):
        self.moves.append((dx, dy))

    def mouse_click(self):
        self.clicks += 1

    def type_text(self, text):
        self.typed.append(text)

    def press_key(self, usage):
        return 0.0

    def _send_with_response(self, command):
        return "OK"


class BlindDetector:
    """A detector that never sees the AssistiveTouch pointer."""

    def detect(self, image, near=None):
        return None


def _driver(capture, control, detector):
    session = build_ios_uvc_session(
        capture,
        control,
        frame_size=(200, 200),
        screen_size=(1080, 2400),
        detector=detector,
    )
    return IOSUvcHidDriver(session)


def _actuator(driver):
    ctx = SimpleNamespace(
        device=SimpleNamespace(device_width=1080, device_height=2400),
        _active_driver=driver,
    )
    from artemis.controllers.unified_controller import UnifiedMobileController

    return AdbActuator(ctx, controller=UnifiedMobileController(ctx))


@pytest.mark.asyncio
async def test_click_refuses_to_fire_without_a_visible_pointer_and_reports_it():
    capture, control = FakeCapture(), FakeEsp32()
    actuator = _actuator(_driver(capture, control, BlindDetector()))
    session = ActionSession(build_action_server(actuator))
    await session.start()
    try:
        result = await session.call("click", {"target": [500, 500], "times": 1, "delay_ms": 100})
    finally:
        await session.aclose()

    assert result.ok is False
    assert result.incident == "pointer_missing"
    assert control.clicks == 0, "no verified pointer means no click"
    assert capture.grabs >= 1


@pytest.mark.asyncio
async def test_a_verified_pointer_that_changes_nothing_reports_no_effect():
    from artemis.reflex.vision import Pointer

    class CenteredDetector:
        def detect(self, image, near=None):
            return Pointer(0.5, 0.5, 0.95, "fake")

    capture, control = FakeCapture(), FakeEsp32()
    actuator = _actuator(_driver(capture, control, CenteredDetector()))
    session = ActionSession(build_action_server(actuator))
    await session.start()
    try:
        result = await session.call("click", {"target": [500, 500], "times": 1, "delay_ms": 100})
    finally:
        await session.aclose()

    # The frame never changes, so the verified click cannot be confirmed.
    assert result.ok is False
    assert result.incident == "no_effect"
    assert control.clicks == 1


@pytest.mark.asyncio
async def test_input_text_goes_through_the_hid_keyboard():
    capture, control = FakeCapture(), FakeEsp32()
    actuator = _actuator(_driver(capture, control, BlindDetector()))
    session = ActionSession(build_action_server(actuator))
    await session.start()
    try:
        result = await session.call("input_text", {"text": "珍珠奶茶", "clear_exist": False})
    finally:
        await session.aclose()

    assert result.ok is True
    assert control.typed == ["珍珠奶茶"]


def test_the_session_declares_the_capabilities_the_safety_net_reads():
    from artemis.devices.ios_uvc import tap_target
    from artemis.perception.base import Target

    capture, control = FakeCapture(), FakeEsp32()
    session = build_ios_uvc_session(
        capture, control, frame_size=(200, 200), screen_size=(1080, 2400)
    )
    sink = session.control_for(absolute=False)

    assert session.capabilities.requires_pointer_precheck is True
    assert session.capabilities.supports_ui_tree is False
    assert session.capabilities.supports_absolute_tap is False
    assert sink.capabilities.supports_relative_pointer is True
    # Mapping a normalized OCR box to screen pixels is the only "absolute" surface.
    result = asyncio.run(tap_target(session, Target("x", (0.4, 0.4, 0.6, 0.6), 0.9, "ocr")))
    assert isinstance(result, DriverActionResult)


class TrackingEsp32(FakeEsp32):
    """The HID board, with the pointer position it would move on the phone."""

    def __init__(self, gain=1.8, frame=(449, 974)):
        super().__init__()
        self.gain = gain
        self.frame = frame
        self.x = 0.0
        self.y = 0.0

    def mouse_home(self):
        super().mouse_home()
        self.x = self.y = 0.0

    def mouse_move(self, dx, dy):
        super().mouse_move(dx, dy)
        width, height = self.frame
        self.x = max(0.0, min(float(width), self.x + dx * self.gain))
        self.y = max(0.0, min(float(height), self.y + dy * self.gain))


class TrackingDetector:
    """Reads the pointer where the fake board actually left it, in frame pixels."""

    def __init__(self, control, frame=(449, 974)):
        self.control = control
        self.frame = frame

    def detect(self, image, near=None):
        from artemis.reflex.vision import Pointer

        width, height = self.frame
        return Pointer(self.control.x / width, self.control.y / height, 0.95, "fake")


class ClickChangesCapture:
    """A card whose picture changes exactly when the phone receives a click."""

    def __init__(self, control):
        self.control = control
        self.grabs = 0

    def grab(self):
        self.grabs += 1
        color = (200, 40, 40) if self.control.clicks else (120, 120, 120)
        return SimpleNamespace(image=_frame(color), width=449, height=974, captured_at=1.0)


@pytest.mark.asyncio
async def test_a_screen_space_target_is_reached_and_clicked_in_frame_space():
    control = TrackingEsp32()
    capture = ClickChangesCapture(control)
    session = build_ios_uvc_session(
        capture,
        control,
        frame_size=(449, 974),
        screen_size=(1080, 2400),
        detector=TrackingDetector(control),
    )
    driver = IOSUvcHidDriver(session)
    actuator = _actuator(driver)
    action_session = ActionSession(build_action_server(actuator))
    await action_session.start()
    try:
        # normalized (500, 500) on a 1080x2400 screen == frame (224, 487)
        result = await action_session.call(
            "click", {"target": [500, 500], "times": 1, "delay_ms": 100}
        )
    finally:
        await action_session.aclose()

    assert result.ok is True
    assert control.clicks == 1
    assert abs(control.x - 224) <= 8 and abs(control.y - 487) <= 8, (
        f"the pointer must converge inside the frame (got {control.x:.0f},{control.y:.0f})"
    )
