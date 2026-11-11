"""ESP32 HID control as a standard async control sink."""

from __future__ import annotations

import asyncio
import time

from artemis.control.base import ActionResult
from artemis.core.coordinates import CoordinateSpace, PointRef
from artemis.core.capabilities import (
    CapabilityDeclaration,
    CapabilityId,
    CapabilityManifest,
)
from artemis.core.capability_registry import ProbeResult
from artemis.core.capabilities import CapabilitySet
from third_party.mobile_use.utils.logger import get_logger

logger = get_logger(__name__)

#: Where a drag starts, as a fraction of the frame height. iOS reads a downward drag
#: from the very top as Control Center and an upward drag from the bottom as Home (it
#: leaves the app), and a pointer parked on an edge has no room to drag at all. An
#: upward drag therefore starts low in the frame, a downward one high, and both stay
#: clear of the edge they move away from.

#: One arrow press scrolls roughly this far in a scroll view (measured ~170 px on the
#: Taobao shop page). It only sizes the press burst; the frame check decides.
#: A long burst would walk the focus ring out of the scroll container.
#: A scroll moves at least this much of the frame. The first arrow press can merely
#: put the focus ring around a container — a change, but not a scroll.

#: iOS's interactive pop gesture (what BACK means on this rig) must start on the
#: *physical* left edge and travel past the ~1/3-width completion threshold before
#: the button is released. Mid-height keeps it clear of the Control Center / Home
#: gesture bands. A short, paced drag is more reliably recognised than a flick.


class HidControlSink:
    """Relative mouse/keyboard sink. `tap_abs` is a target API, not absolute HID.

    ``tap_abs`` receives a **device-pixel** target (the action layer converts the
    agent's 0–1000 coordinates against the screen size). The relative-pointer
    controller, however, compares its target against the pointer it detects in the
    **captured frame**, which is the cropped phone picture — a different pixel grid.
    Supplying ``geometry`` makes the sink translate between the two; without it the
    target is passed through unchanged (a sink whose caller already speaks frame
    pixels, and every unit test that drives the controller directly).
    """

    capabilities = CapabilitySet(
        supports_relative_pointer=True,
        requires_pointer_precheck=True,
        supports_text_input=True,
    )

    def __init__(self, session, controller, *, geometry=None):
        self.session = session
        self.controller = controller
        self.geometry = geometry
        self._control = session.control

    def _frame_point(self, x: int, y: int) -> tuple[int, int]:
        """Device pixels -> capture-frame pixels (identity when no geometry is known)."""
        if self.geometry is None:
            return int(x), int(y)
        point = self.geometry.pointer_transform().apply(
            PointRef(CoordinateSpace.DEVICE_PIXEL, int(x), int(y))
        )
        return int(round(point.x)), int(round(point.y))

    @staticmethod
    def _metrics(result, *, target: tuple[int, int], frame_target: tuple[int, int]) -> dict:
        """The evidence a graph can read: where the pointer was, how far off, how old."""
        pointer = getattr(result, "pointer", None)
        metrics: dict[str, float | str] = {
            "target_x": float(target[0]),
            "target_y": float(target[1]),
            "frame_x": float(frame_target[0]),
            "frame_y": float(frame_target[1]),
            "attempts": float(getattr(result, "attempts", 0) or 0),
        }
        if pointer is not None:
            metrics["pointer_x"] = float(getattr(pointer, "x", 0.0)) * 1000.0
            metrics["pointer_y"] = float(getattr(pointer, "y", 0.0)) * 1000.0
            metrics["pointer_confidence"] = float(getattr(pointer, "confidence", 0.0))
        error = getattr(result, "error_px", None)
        if isinstance(error, (int, float)):
            metrics["error_px"] = float(error)
        age = getattr(result, "frame_age_s", None)
        if isinstance(age, (int, float)):
            metrics["frame_age_s"] = float(age)
        crop_change = getattr(result, "crop_change", None)
        if isinstance(crop_change, (int, float)):
            metrics["crop_change"] = float(crop_change)
        motion = getattr(result, "motion", None)
        if motion is not None:
            metrics["motion_segments"] = float(getattr(motion, "segments", 0) or 0)
            metrics["motion_observations"] = float(getattr(motion, "observations", 0) or 0)
            phases = getattr(motion, "phases", None) or []
            metrics["motion_phases"] = ",".join(str(phase) for phase in phases)
            prediction = getattr(motion, "prediction_error_px", None)
            if isinstance(prediction, (int, float)):
                metrics["motion_prediction_error_px"] = float(prediction)
            metrics["motion_global_recoveries"] = float(
                getattr(motion, "global_recoveries", 0) or 0
            )
        return metrics

    @property
    def capability_manifest(self) -> CapabilityManifest:
        declarations = [
            CapabilityDeclaration(CapabilityId.SERIAL_COMMAND),
            CapabilityDeclaration(CapabilityId.USB_HID),
            CapabilityDeclaration(CapabilityId.POINTER_RELATIVE),
        ]
        if self.capabilities.supports_text_input:
            declarations.extend(
                (
                    CapabilityDeclaration(CapabilityId.TEXT_INPUT),
                    CapabilityDeclaration(CapabilityId.KEYBOARD_KEY),
                )
            )
        if getattr(self.controller, "absolute_ready", False):
            declarations.append(CapabilityDeclaration(CapabilityId.POINTER_ABSOLUTE))
        return CapabilityManifest("esp32_hid", "1", tuple(declarations))

    def probe_capabilities(self) -> dict[str, ProbeResult]:
        """Ask the HID board which interfaces are ready; never sends an action."""

        control = getattr(self, "_control", None)
        preflight = getattr(control, "preflight", None)
        if not callable(preflight):
            return {}
        now = time.time()
        try:
            state = preflight()
        except (OSError, RuntimeError, ValueError, AttributeError) as exc:
            return {
                CapabilityId.SERIAL_COMMAND: ProbeResult(
                    False, "esp32_hid", f"preflight_failed: {exc}", now
                )
            }
        probes = {CapabilityId.SERIAL_COMMAND: ProbeResult(True, "esp32_hid", None, now)}
        if int(state.get("mounted") or 0) == 1:
            probes[CapabilityId.USB_HID] = ProbeResult(True, "esp32_hid", None, now)
        if int(state.get("mouse") or 0) == 1:
            probes[CapabilityId.POINTER_RELATIVE] = ProbeResult(True, "esp32_hid.mouse", None, now)
            if getattr(self.controller, "absolute_ready", False):
                probes[CapabilityId.POINTER_ABSOLUTE] = ProbeResult(
                    True, "esp32_hid.absolute", None, now
                )
        if int(state.get("keyboard") or 0) == 1:
            probes[CapabilityId.KEYBOARD_KEY] = ProbeResult(True, "esp32_hid.keyboard", None, now)
            probes[CapabilityId.TEXT_INPUT] = ProbeResult(True, "esp32_hid.keyboard", None, now)
        return probes

    def close(self) -> None:
        """Release held inputs and close the HID transport (idempotent)."""

        control = getattr(self, "_control", None)
        release = getattr(control, "release_all", None)
        if callable(release):
            try:
                release()
            except (OSError, RuntimeError, ValueError, AttributeError) as exc:
                logger.warning(f"Releasing HID input state failed: {exc}")
        closer = getattr(control, "close", None)
        if callable(closer):
            try:
                closer()
            except (OSError, RuntimeError, ValueError, AttributeError) as exc:
                logger.warning(f"Closing HID control failed: {exc}")
        pointer = getattr(self, "session", None)
        if pointer is not None:
            pointer.connected = False

    async def tap_abs(self, x: int, y: int) -> ActionResult:
        frame_x, frame_y = self._frame_point(x, y)
        result = await asyncio.to_thread(self.controller.tap, frame_x, frame_y)
        metrics = self._metrics(result, target=(x, y), frame_target=(frame_x, frame_y))
        if result.ok:
            return ActionResult(True, "tap_abs", detail=result.message, metrics=metrics)
        return ActionResult(
            False,
            "tap_abs",
            result.incident or "no_effect",
            result.message,
            metrics,
        )

    async def tap_rel(self, dx: int, dy: int) -> ActionResult:
        await asyncio.to_thread(self.session.move, dx, dy)
        return ActionResult(True, "tap_rel")

    async def swipe(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration_ms: int = 800,
    ) -> ActionResult:
        """A generic pointer drag; iOS safe-anchor/keyboard policy lives in IOSControlSink."""
        dx = int(end_x) - int(start_x)
        dy = int(end_y) - int(start_y)
        try:
            swipe = getattr(self._control, "swipe_mouse", None)
            if swipe is not None:
                await asyncio.to_thread(swipe, dx, dy, duration_ms=duration_ms)
            else:

                def run():
                    self._control.mouse_down()
                    self._control.mouse_move(dx, dy)
                    self._control.mouse_up()

                await asyncio.to_thread(run)
        except (OSError, RuntimeError, ValueError, AttributeError) as exc:
            return ActionResult(False, "swipe", "swipe_failed", str(exc))
        return ActionResult(True, "swipe")

    async def input_text(self, text: str) -> ActionResult:
        await asyncio.to_thread(self._control.type_text, text)
        return ActionResult(True, "input_text")

    async def clear_text(self) -> ActionResult:
        await asyncio.to_thread(self._control.clear_text)
        return ActionResult(True, "clear_text")

    async def press_key(self, key) -> ActionResult:
        # BACK is deliberately *not* handled here: the HID keyboard has no Back key.
        # IOSUvcHidDriver routes BACK to go_back() (the edge gesture) before it can
        # reach this method; a direct keyboard call still gets the refusal below.
        press = getattr(self._control, "press_named_key", None)
        if press is None:
            await asyncio.to_thread(self._control._send_with_response, f"key {key}")
            return ActionResult(True, "press_key")
        try:
            await asyncio.to_thread(press, key)
        except ValueError as exc:
            return ActionResult(False, "press_key", "unsupported_key", str(exc))
        return ActionResult(True, "press_key")
