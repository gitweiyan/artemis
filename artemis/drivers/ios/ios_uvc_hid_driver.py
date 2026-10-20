"""Wrap the iOS UVC + ESP32 HID session as an Artemis BaseDeviceDriver."""

from __future__ import annotations

import base64
import time

from artemis.control.base import ActionResult
from artemis.devices.capabilities import CapabilitySet
from artemis.drivers.base import BaseDeviceDriver, ScreenData


class IOSUvcHidDriver(BaseDeviceDriver):
    """iOS perception via UVC, control via relative ESP32 HID."""

    capabilities = CapabilitySet(
        supports_relative_pointer=True,
        requires_pointer_precheck=True,
        supports_text_input=True,
    )

    def __init__(self, session, *, device_id: str = "ios-uvc-hid"):
        self.session = session
        self._device_id = device_id
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

    async def tap(self, x, y, duration_ms=100, times=1, delay_ms=100) -> bool:
        result = await self._relative_sink().tap_abs(int(x), int(y))
        return result.ok

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
