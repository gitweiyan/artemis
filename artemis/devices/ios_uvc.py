"""Compose the iOS UVC perception + ESP32 HID control session."""

from __future__ import annotations

import os

from artemis.control.base import ActionResult
from artemis.control.hid import HidControlSink
from artemis.devices.geometry import ScreenGeometry
from artemis.devices.session import DeviceSession
from artemis.reflex.mouse import PointerSession, SessionTapController
from artemis.perception.base import Target
from artemis.perception.uvc import UvcSource
from artemis.perception.ios_targets import icon_above_label
from artemis.reflex.locate_anything import (
    DEFAULT_LOCATE_URL,
    LocateAnythingDetector,
)
from artemis.reflex.vision import BluePointerDetector, CompositePointerDetector, PointerTracker


class _GrabLatest:
    """Adapt the UVC capture to SessionTapController's ``grab_latest`` seam.

    A persistent stream can hand over its newest queued frame; a one-shot capture can
    only grab a fresh one. Preferring ``grab_latest`` is what lets the controller see
    the pointer right after the move that woke it, instead of a frame that predates it.
    """

    def __init__(self, capture):
        self.capture = capture

    def grab_latest(self):
        latest = getattr(self.capture, "grab_latest", None)
        if callable(latest):
            return latest()
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
    assistive_button_box: tuple[int, int, int, int] | None = None,
) -> DeviceSession:
    # One URL for both locators: the pointer detector chain and the perception target
    # locator. `off`/`none`/`0` disables the service everywhere.
    locate_url = os.environ.get("ARTEMIS_IOS_LOCATE_URL", DEFAULT_LOCATE_URL).strip()
    if locate_url.lower() in ("off", "none", "0"):
        locate_url = ""
    pointer = PointerSession(control, frame_size=frame_size, gain=pointer_gain)
    if detector is None:
        detectors = [BluePointerDetector()]
        if locate_url:
            detectors.insert(0, LocateAnythingDetector(locate_url))
        detector = CompositePointerDetector(detectors)
    controller = SessionTapController(
        pointer,
        capture=_GrabLatest(capture),
        detector=detector,
        # The AssistiveTouch pointer is sometimes grey and half-clipped at the screen
        # edge, which no colour/shape model catches reliably; the nudge/diff locator
        # needs no appearance model at all, so it backs the colour detector up.
        tracker=PointerTracker(menu_box=assistive_button_box or (0, 0, 0, 0)),
    )
    geometry = ScreenGeometry(frame_size, screen_size)
    return DeviceSession(
        perception=[
            UvcSource(
                capture,
                ocr_engine=ocr_engine,
                target_adjuster=icon_above_label,
                locate_url=locate_url,
            )
        ],
        control=[HidControlSink(pointer, controller, geometry=geometry)],
        geometry=geometry,
        pointer=pointer,
    )


async def tap_target(session: DeviceSession, target: Target) -> ActionResult:
    """Map a normalized OCR target to screen pixels and tap via relative HID."""
    if session.geometry is None:
        raise ValueError("session has no ScreenGeometry")
    x, y = session.geometry.normalized_box_center(target.box)
    sink = session.control_for(absolute=False)
    return await sink.tap_abs(x, y)
