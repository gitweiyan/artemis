"""Compose the iOS UVC perception + ESP32 HID control session."""

from __future__ import annotations

from artemis.control.base import ActionResult
from artemis.control.hid import HidControlSink
from artemis.devices.geometry import ScreenGeometry
from artemis.devices.session import DeviceSession
from artemis.reflex.mouse import PointerSession, SessionTapController
from artemis.perception.base import Target
from artemis.perception.uvc import UvcSource
from artemis.reflex.vision import RedPointerDetector


class _GrabLatest:
    """Adapt the one-shot UVC capture to SessionTapController's grab_latest seam."""

    def __init__(self, capture):
        self.capture = capture

    def grab_latest(self):
        return self.capture.grab()


def build_ios_uvc_session(
    capture,
    control,
    *,
    frame_size: tuple[int, int] = (449, 974),
    screen_size: tuple[int, int] = (1080, 2400),
    pointer_gain: tuple[float, float] = (1.8, 1.8),
    detector=None,
    ocr_engine=None,
) -> DeviceSession:
    pointer = PointerSession(control, frame_size=frame_size, gain=pointer_gain)
    controller = SessionTapController(
        pointer,
        capture=_GrabLatest(capture),
        detector=detector or RedPointerDetector(),
    )
    geometry = ScreenGeometry(frame_size, screen_size)
    return DeviceSession(
        perception=[UvcSource(capture, ocr_engine=ocr_engine)],
        control=[HidControlSink(pointer, controller, geometry=geometry)],
        geometry=geometry,
    )


async def tap_target(session: DeviceSession, target: Target) -> ActionResult:
    """Map a normalized OCR target to screen pixels and tap via relative HID."""
    if session.geometry is None:
        raise ValueError("session has no ScreenGeometry")
    x, y = session.geometry.normalized_box_center(target.box)
    sink = session.control_for(absolute=False)
    return await sink.tap_abs(x, y)
