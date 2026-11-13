"""Compose the iOS UVC perception + ESP32 HID control session."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from artemis.adapters.pointer import RelativePointerAdapter
from artemis.control.base import ActionResult
from artemis.control.ios_hid import IOSControlSink
from artemis.devices.geometry import ScreenGeometry
from artemis.devices.session import DeviceSession
from artemis.drivers.ios.extension import IOSTargetAdjuster, build_ios_extension
from artemis.adapters.relative_pointer import PointerSession, SessionTapController
from artemis.perception.base import Target
from artemis.perception.uvc import UvcSource
from artemis.perception.locate_anything import (
    DEFAULT_LOCATE_URL,
    LocateAnythingDetector,
)
from artemis.perception.pointer_detection import (
    BluePointerDetector,
    CompositePointerDetector,
    PointerTracker,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from artemis.core.identity import DeviceDescriptor
    from artemis.runtime.resource_lease import LeaseSet


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
    target_adjuster=None,
    assistive_button_box: tuple[int, int, int, int] | None = None,
    descriptor: DeviceDescriptor | None = None,
    leases: LeaseSet | None = None,
    session_id: str | None = None,
) -> DeviceSession:
    # One URL for both locators: the pointer detector chain and the perception target
    # locator. `off`/`none`/`0` disables the service everywhere.
    locate_url = os.environ.get("ARTEMIS_IOS_LOCATE_URL", DEFAULT_LOCATE_URL).strip()
    if locate_url.lower() in ("off", "none", "0"):
        locate_url = ""
    pointer = PointerSession(
        control,
        frame_size=frame_size,
        gain=pointer_gain,
        absolute_rotated=True,
    )
    target_locator = None
    if detector is None:
        # Size/area windows follow the capture: the defaults were measured on a 449-wide
        # frame, and a 2x capture makes the same ring twice as wide (see `scaled_for`).
        detectors = [BluePointerDetector.scaled_for(frame_size)]
        if locate_url:
            target_locator = LocateAnythingDetector(
                locate_url, timeout_s=10.0, cooldown_s=4.0, max_cooldown_s=60.0
            )
            # The pointer loop keeps its short 4 s budget; a cold target locator may
            # need the model load on its first request, so it gets 10 s.
            detectors.insert(0, LocateAnythingDetector(locate_url))
            # The first inference after an idle period loads the model (measured 3.6 s and
            # 25.5 s here). Pay it now, in the background, instead of inside the first
            # click's 10 s budget — a cold timeout used to disable target resolution for
            # the rest of the run (2026-11-06 15:58).
            from artemis.perception.locate_anything import warm_up_locate_anything

            warm_up_locate_anything(locate_url)
        detector = CompositePointerDetector(detectors)
    # The AssistiveTouch pointer is sometimes grey and half-clipped at the screen
    # edge, which no colour/shape model catches reliably; the nudge/diff locator
    # needs no appearance model at all, so it backs the colour detector up.
    tracker = PointerTracker(menu_box=assistive_button_box or (0, 0, 0, 0))
    controller = SessionTapController(
        pointer,
        capture=_GrabLatest(capture),
        detector=detector,
        tracker=tracker,
    )
    geometry = ScreenGeometry(frame_size, screen_size)
    pointer_adapter = RelativePointerAdapter(
        pointer,
        controller,
        capture=capture,
        geometry=geometry,
        detector=detector,
        tracker=tracker,
    )
    session = DeviceSession(
        perception=[
            UvcSource(
                capture,
                ocr_engine=ocr_engine,
                target_adjuster=target_adjuster or IOSTargetAdjuster(),
                locator=target_locator,
            )
        ],
        control=[IOSControlSink(pointer, controller, geometry=geometry)],
        geometry=geometry,
        descriptor=descriptor,
        leases=leases,
        session_id=session_id,
        adapter_state={
            "pointer": pointer,
            "pointer_adapter": pointer_adapter,
            "target_locator": target_locator,
        },
    )
    session.adapter_state["platform_extension"] = build_ios_extension(
        control,
        sink=session.control_for(absolute=False),
        capture=capture,
        locator=target_locator,
    )
    return session


async def tap_target(session: DeviceSession, target: Target) -> ActionResult:
    """Map a normalized OCR target to screen pixels and tap via relative HID."""
    if session.geometry is None:
        raise ValueError("session has no ScreenGeometry")
    session.validate_target(target)
    x, y = session.geometry.normalized_box_center(target.box)
    sink = session.control_for(absolute=False)
    return await sink.tap_abs(x, y)
