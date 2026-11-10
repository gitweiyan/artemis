"""Wrap the iOS UVC + ESP32 HID session as an Artemis BaseDeviceDriver."""

from __future__ import annotations

import asyncio
import base64
import time
from dataclasses import dataclass, replace

from artemis.control.base import ActionResult
from artemis.devices.capabilities import CapabilitySet
from artemis.devices.session import DeviceSessionError
from artemis.drivers.base import BaseDeviceDriver, ScreenData
from artemis.perception.incidents import (
    UVC_NO_SIGNAL,
    UnhealthyFrame,
    incident_for_error,
    incident_for_health,
)
from artemis.reflex.ios_apps import IOSAppLauncher, AppLaunchResult
from artemis.perception.pointer_detection import CompositePointerDetector
from artemis.safety.pointer import check_in_box
from third_party.mobile_use.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class PointerValidation:
    ok: bool
    incident: str | None
    error_px: float | None
    pointer: tuple[float, float] | None


def _health_from_message(message: str) -> str | None:
    """The ``Frame.health`` value a ``DeviceSessionError`` complained about, if any."""
    marker = "frame health="
    index = message.find(marker)
    if index < 0:
        return None
    tail = message[index + len(marker) :].lstrip("=\"'")
    return tail.split("'")[0].split('"')[0].split(";")[0].strip() or None


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
        supports_app_launch=True,
    )

    def __init__(self, session, *, device_id: str = "ios-uvc-hid", detector=None):
        self.session = session
        self._device_id = device_id
        self.detector = detector or _session_detector(session) or CompositePointerDetector()
        # What *this rig* can do: the session aggregates its perception sources and
        # control sinks. The class-level declaration above stays the contract default
        # (and the thing tests read on the class); a driver instance reports the truth,
        # so a log or a caller never has to guess whether OCR is actually available.
        session_capabilities = getattr(session, "capabilities", None)
        if session_capabilities is not None:
            # The session aggregates its sources/sinks; driver-only affordances (no app
            # package list, no UI tree) stay the driver's own declaration.
            declared = type(self).capabilities
            self.capabilities = replace(
                session_capabilities,
                supports_app_launch=declared.supports_app_launch,
                supports_ui_tree=declared.supports_ui_tree,
            )
        self._screen_size = session.geometry.screen_size if session.geometry else (1080, 2400)

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def screen_size(self) -> tuple[int, int]:
        return self._screen_size

    async def connect(self) -> None:
        opener = getattr(self.session, "open", None)
        if callable(opener):
            opener()
        pointer = getattr(self.session, "pointer", None)
        if pointer is None:
            return
        ritual = getattr(pointer, "connect", None)
        if callable(ritual):
            # One place for the whole ritual: preflight -> iOS Home (Cmd+H) -> pointer
            # home. It belongs here, before anyone observes a frame, because Cmd+H
            # replaces the screen: a caller that already chose a target on the previous
            # screen would tap blind (2026-11-05: the first click of a run dismissed
            # Settings mid-tap; `reflex/mouse.py::_connect_pointer` is the backstop).
            await asyncio.to_thread(ritual)
        else:
            pointer.connected = True
            pointer.home()
        prober = getattr(self.session, "probe_capabilities", None)
        if callable(prober):
            try:
                await prober()
            except (OSError, RuntimeError, ValueError, AttributeError) as exc:
                logger.warning(f"Capability probes failed after connect: {exc}")

    async def _prepare_pointer(self) -> None:
        """Run the pointer session's connect ritual before the first observed frame.

        Best-effort: a dead HID link must not hide the screen, so a failed prep is logged
        and the frame is still returned. The tap path has its own ``screen_changed``
        refusal for the case where a connect still happens inside a click.
        """
        pointer = getattr(self.session, "pointer", None)
        if pointer is None or getattr(pointer, "connected", True):
            return
        ritual = getattr(pointer, "connect", None)
        if not callable(ritual):
            return
        try:
            await asyncio.to_thread(ritual)
        # The serial/HID stack raises these; an unexpected exception is a bug and should
        # surface instead of being swallowed by a best-effort prep.
        except (AttributeError, RuntimeError, OSError, ValueError) as exc:
            logger.warning(
                f"iOS pointer preflight failed before the first frame ({exc});"
                " continuing so the screen is still observable"
            )

    async def disconnect(self) -> None:
        closer = getattr(self.session, "close", None)
        if callable(closer):
            await closer()
            return
        pointer = getattr(self.session, "pointer", None)
        if pointer is not None:
            pointer.connected = False

    async def snapshot_frame(self):
        """The one frame this driver is allowed to act on, or a named failure.

        A ``DeviceSession`` tries its sources and raises ``DeviceSessionError`` when none
        is healthy; a bare session-like object (tests, a future single-source rig) is
        gated here instead. Either way a black frame never becomes an "observation".
        """
        await self._prepare_pointer()
        snapshot = getattr(self.session, "snapshot", None)
        if callable(snapshot):
            try:
                return await snapshot()
            except DeviceSessionError as exc:
                # "health=<value>" means a frame arrived but is unusable; anything else
                # is a capture failure whose message decides timeout vs no signal.
                health = _health_from_message(str(exc))
                incident = incident_for_health(health) if health else incident_for_error(exc)
                raise UnhealthyFrame(incident, str(exc)) from exc
            except UnhealthyFrame:
                raise
            except Exception as exc:  # noqa: BLE001 - a capture failure is data
                raise UnhealthyFrame(incident_for_error(exc), str(exc)) from exc
        try:
            frame = await self.session.perception[0].snapshot()
        except Exception as exc:  # noqa: BLE001
            raise UnhealthyFrame(incident_for_error(exc), str(exc)) from exc
        incident = incident_for_health(getattr(frame, "health", "ok"))
        if incident:
            raise UnhealthyFrame(incident, f"frame health={getattr(frame, 'health', None)!r}")
        return frame

    async def get_screen_data(self, skip_settling: bool = False) -> ScreenData:
        """The phone's screen, captured through the card.

        ``width``/``height`` must be the **device screen** size, not the cropped frame
        size: the observation layer writes them back onto ``ctx.device`` and every
        normalized (0–1000) coordinate is converted against them. Reporting the frame
        (449x974) shrank that grid by 2.4x and sent every tap to the top-left corner
        (measured live 2026-10-21: a tap meant for 微信 landed on 日历).
        """
        frame = await self.snapshot_frame()
        width, height = self._screen_size
        return ScreenData(
            screenshot_bytes=frame.image,
            screenshot_base64=base64.b64encode(frame.image).decode("ascii"),
            ui_elements=[],
            width=width,
            height=height,
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

    def pointer_control(self):
        """A reflex ``Control`` for this rig: absolute gestures, relative pointer.

        Skills emit device-pixel taps and swipes; this adapter turns them into the same
        verified pointer path the agent uses, so a skill can run on a phone without a
        digitizer.
        """
        from artemis.reflex.pointer_control import PointerControl

        sink = self._relative_sink()
        return PointerControl(sink.controller, session=sink.session, geometry=self.session.geometry)

    async def validate_pointer(self, target, *, tolerance_px: float = 8.0) -> PointerValidation:
        """Read-only precheck: is the pointer on this target *right now*?

        The rule itself lives in ``artemis.safety.pointer`` so the smoke, the driver and
        the controller cannot drift apart on what "on target" means.
        """
        frame = await self.snapshot_frame()
        left, top, right, bottom = target.box
        center_norm = ((left + right) / 2, (top + bottom) / 2)
        pointer = self.detector.detect(frame.image, near=center_norm)
        if pointer is None:
            return PointerValidation(False, "pointer_missing", None, None)
        px, py = pointer.x * frame.width, pointer.y * frame.height
        box = (
            left * frame.width,
            top * frame.height,
            right * frame.width,
            bottom * frame.height,
        )
        verdict = check_in_box(box, px, py, tolerance_px=tolerance_px)
        return PointerValidation(verdict.ok, verdict.incident, verdict.error_px, (px, py))

    async def validate_pointer_by_diff(
        self, target, *, tolerance_px: float = 8.0
    ) -> PointerValidation:
        """Locate a pointer the colour detector cannot see (grey AssistiveTouch ring).

        The controller's tracker nudges the pointer and diffs two frames, so this works
        without any appearance model. It moves the pointer as part of the measurement;
        the smoke uses it only after the read-only precheck reported ``pointer_missing``.
        """
        sink = self._relative_sink()
        controller = getattr(sink, "controller", None)
        tracker = getattr(controller, "tracker", None)
        if controller is None or tracker is None:
            return PointerValidation(False, "pointer_missing", None, None)
        frame = await self.snapshot_frame()
        try:
            later, pointer = await asyncio.to_thread(controller._observe_by_diff, frame)
        except (OSError, RuntimeError, ValueError, AttributeError):
            return PointerValidation(False, "pointer_missing", None, None)
        if pointer is None:
            return PointerValidation(False, "pointer_missing", None, None)
        width = int(getattr(later, "width", frame.width) or frame.width)
        height = int(getattr(later, "height", frame.height) or frame.height)
        px, py = pointer.x * width, pointer.y * height
        left, top, right, bottom = target.box
        verdict = check_in_box(
            (left * width, top * height, right * width, bottom * height),
            px,
            py,
            tolerance_px=tolerance_px,
        )
        return PointerValidation(verdict.ok, verdict.incident, verdict.error_px, (px, py))

    async def tap_with_result(self, x, y) -> ActionResult:
        """Return the structured precheck/click result for Validator incidents.

        The pointer loop cannot verify anything without frames, so a capture failure is
        reported as the tap's incident (``capture_timeout`` / ``uvc_no_signal``) instead
        of raising through the controller and losing the reason.
        """
        try:
            return await self._relative_sink().tap_abs(int(x), int(y))
        except UnhealthyFrame as exc:
            return ActionResult(
                False,
                "tap_abs",
                exc.incident,
                str(exc),
                {"target_x": float(x), "target_y": float(y)},
            )
        except DeviceSessionError as exc:
            return ActionResult(
                False,
                "tap_abs",
                UVC_NO_SIGNAL,
                str(exc),
                {"target_x": float(x), "target_y": float(y)},
            )
        except Exception as exc:  # noqa: BLE001 - a device failure is an incident
            return ActionResult(
                False,
                "tap_abs",
                incident_for_error(exc),
                str(exc),
                {"target_x": float(x), "target_y": float(y)},
            )

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

    async def erase_text(self, nb_chars: int | None = None) -> bool:
        """Clear the focused field on iOS: Cmd+A + Backspace (nb_chars ignored)."""
        result = await self._relative_sink().clear_text()
        return result.ok

    async def press_key(self, key) -> bool:
        # iOS has no Back *key*: BACK is the interactive pop gesture, so it must
        # bypass the HID keyboard (which deliberately refuses the name) and reach
        # the sink's edge swipe. Every other key still belongs to the keyboard.
        if str(key).strip().lower() == "back":
            sink = self._relative_sink()
            go_back = getattr(sink, "go_back", None)
            if callable(go_back):
                return (await go_back()).ok
        result = await self._relative_sink().press_key(key)
        return result.ok

    async def open_app(self, app_name: str) -> AppLaunchResult:
        """iOS app launch through Spotlight, verified by the UVC frame when available."""
        sink = self._relative_sink()
        control = getattr(sink, "_control", None)
        if control is None:
            return AppLaunchResult(False, app_name, app_name, error="no_hid_control")
        source = self.session.perception[0] if self.session.perception else None
        capture = getattr(source, "capture", None)
        launcher = IOSAppLauncher(control)
        return await asyncio.to_thread(launcher.open_app, app_name, capture=capture)

    async def launch_app(self, package_name) -> bool:
        result = await self.open_app(package_name)
        return bool(result.ok)

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
