"""Wrap the iOS UVC + ESP32 HID session as an Artemis BaseDeviceDriver."""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass

from artemis.control.base import ActionResult
from artemis.devices.capabilities import CapabilitySet
from artemis.drivers.base import BaseDeviceDriver, ScreenData
from artemis.reflex.vision import RedPointerDetector


@dataclass(frozen=True)
class PointerValidation:
    ok: bool
    incident: str | None
    error_px: float | None
    pointer: tuple[float, float] | None


def _session_detector(session):
    """The pointer detector the session already uses, if any.

    One rig has one pointer detector: a second instance in the driver would be a
    silently different configuration (and a second place to fix when the pointer's
    appearance changes).
    """
    for sink in getattr(session, "control", []) or []:
        detector = getattr(getattr(sink, "controller", None), "detector", None)
        if detector is not None:
            return detector
    return None


class IOSUvcHidDriver(BaseDeviceDriver):
    """iOS perception via UVC, control via relative ESP32 HID."""

    capabilities = CapabilitySet(
        supports_relative_pointer=True,
        requires_pointer_precheck=True,
        supports_text_input=True,
        supports_app_launch=False,
    )

    def __init__(self, session, *, device_id: str = "ios-uvc-hid", detector=None):
        self.session = session
        self._device_id = device_id
        self.detector = detector or _session_detector(session) or RedPointerDetector()
        # What *this rig* can do: the session aggregates its perception sources and
        # control sinks. The class-level declaration above stays the contract default
        # (and the thing tests read on the class); a driver instance reports the truth,
        # so a log or a caller never has to guess whether OCR is actually available.
        session_capabilities = getattr(session, "capabilities", None)
        if session_capabilities is not None:
            self.capabilities = session_capabilities
        self._screen_size = session.geometry.screen_size if session.geometry else (1080, 2400)

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def screen_size(self) -> tuple[int, int]:
        return self._screen_size

    async def connect(self) -> None:
        pointer = getattr(self.session, "pointer", None)
        if pointer is not None:
            pointer.connected = True
            pointer.home()

    async def disconnect(self) -> None:
        pointer = getattr(self.session, "pointer", None)
        if pointer is not None:
            pointer.connected = False

    async def get_screen_data(self, skip_settling: bool = False) -> ScreenData:
        frame = await self.session.perception[0].snapshot()
        return ScreenData(
            screenshot_bytes=frame.image,
            screenshot_base64=base64.b64encode(frame.image).decode("ascii"),
            ui_elements=[],
            width=frame.width,
            height=frame.height,
            timestamp=getattr(frame, "captured_at", time.time()),
            platform="ios",
        )

    def _relative_sink(self):
        finder = getattr(self.session, "control_for", None)
        if finder is not None:
            return finder(absolute=False)
        for sink in self.session.control:
            if sink.capabilities.supports_relative_pointer:
                return sink
        raise RuntimeError("no relative HID control sink")

    async def validate_pointer(self, target, *, tolerance_px: float = 8.0) -> PointerValidation:
        frame = await self.session.perception[0].snapshot()
        left, top, right, bottom = target.box
        center_norm = ((left + right) / 2, (top + bottom) / 2)
        pointer = self.detector.detect(frame.image, near=center_norm)
        if pointer is None:
            return PointerValidation(False, "pointer_missing", None, None)
        px, py = pointer.x * frame.width, pointer.y * frame.height
        tx, ty = center_norm[0] * frame.width, center_norm[1] * frame.height
        error = ((px - tx) ** 2 + (py - ty) ** 2) ** 0.5
        inside = (
            left * frame.width <= px <= right * frame.width
            and top * frame.height <= py <= bottom * frame.height
        )
        if not inside:
            return PointerValidation(False, "pointer_off_target", error, (px, py))
        if error > tolerance_px:
            return PointerValidation(False, "pointer_not_centered", error, (px, py))
        return PointerValidation(True, None, error, (px, py))

    async def tap_with_result(self, x, y) -> ActionResult:
        """Return the structured precheck/click result for Validator incidents."""
        return await self._relative_sink().tap_abs(int(x), int(y))

    async def tap(self, x, y, duration_ms=100, times=1, delay_ms=100) -> bool:
        return (await self.tap_with_result(x, y)).ok

    async def long_press(self, x, y, duration_ms=1000) -> bool:
        return await self.tap(x, y, duration_ms=duration_ms)

    async def swipe(self, start_x, start_y, end_x, end_y, duration_ms=800) -> bool:
        result = await self._relative_sink().swipe(
            int(start_x), int(start_y), int(end_x), int(end_y), duration_ms
        )
        return result.ok

    async def swipe_direction(self, direction, duration_ms=800) -> bool:
        return False

    async def input_text(self, text, clear_existing=True) -> bool:
        result = await self._relative_sink().input_text(text)
        return result.ok

    async def press_key(self, key) -> bool:
        result = await self._relative_sink().press_key(key)
        return result.ok

    async def launch_app(self, package_name) -> bool:
        return False

    async def stop_app(self, package_name) -> bool:
        return False

    async def get_current_package(self):
        return None

    async def execute_shell(self, command, timeout_seconds=15.0) -> str:
        return ""

    async def start_video_recording(self, output_dir=None) -> None:
        return None

    async def stop_video_recording(self):
        return None
