"""Pure device composition selection (ADR-005, moved to L4).

The rule is a table, not a platform branch:

    app tree + absolute tap -> app control (fastest, most stable)
    no tree, UVC rig        -> UVC perception + relative HID (needs pointer precheck)
    no tree, camera         -> camera + relative HID (absolute last resort)

This module reads no environment, opens no device and imports no capture/control
implementation. The Profile builder turns a :class:`Composition` into objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from artemis.devices.capabilities import CapabilitySet

__all__ = [
    "CAPTURE_MODES",
    "Composition",
    "CompositionError",
    "CompositionRequest",
    "select_composition",
]

#: How a UVC source may be read: one live frame per grab, or one persistent stream.
CAPTURE_MODES = ("frame", "stream")


class CompositionError(RuntimeError):
    """No composition can be built for this device, and why."""


@dataclass(frozen=True)
class CompositionRequest:
    """What the host knows before a device object exists (ADR-005).

    ``capability_ids`` uses the ADR-001 catalog; an empty set means "not probed
    yet" and lets the selector fall back to the legacy CapabilitySet view.
    """

    platform: str
    device_id: str
    capabilities: CapabilitySet = field(default_factory=CapabilitySet)
    screen_size: tuple[int, int] | None = None
    uvc_available: bool = False
    camera_available: bool = False
    capture_mode: str = "frame"
    profile_id: str | None = None
    capability_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Composition:
    """The chosen perception/control composition, and why."""

    kind: str
    perception: tuple[str, ...]
    control: tuple[str, ...]
    requires_pointer_precheck: bool
    capture_mode: str
    reason: str


def _has_absolute_app_control(capabilities: CapabilitySet) -> bool:
    return bool(capabilities.supports_ui_tree and capabilities.supports_absolute_tap)


def _has_relative_pointer(capabilities: CapabilitySet) -> bool:
    return bool(capabilities.supports_relative_pointer)


def select_composition(request: CompositionRequest) -> Composition:
    """The composition for ``request``, or :class:`CompositionError` naming the gap."""
    capabilities = request.capabilities

    if _has_absolute_app_control(capabilities):
        return Composition(
            kind="app",
            perception=("app",),
            control=("app",),
            requires_pointer_precheck=False,
            capture_mode="n/a",
            reason=(
                "the device exposes a UI tree and absolute touch, so the app surface is"
                " the fastest and most stable composition"
            ),
        )

    if not _has_relative_pointer(capabilities):
        raise CompositionError(
            "no usable control for this device: it has neither absolute touch with a UI"
            " tree nor a relative pointer (perception alone cannot act)"
        )

    if request.capture_mode not in CAPTURE_MODES:
        raise CompositionError(
            f"unknown capture mode {request.capture_mode!r}; expected one of"
            f" {', '.join(CAPTURE_MODES)}"
        )

    if request.uvc_available:
        return Composition(
            kind="uvc-hid",
            perception=("uvc",),
            control=("hid",),
            requires_pointer_precheck=True,
            capture_mode=request.capture_mode,
            reason=(
                "no UI tree on this device; a UVC frame plus a relative pointer is the"
                " best available composition, and the pointer is verified before clicking"
            ),
        )

    if request.camera_available:
        return Composition(
            kind="camera-hid",
            perception=("camera",),
            control=("hid",),
            requires_pointer_precheck=True,
            capture_mode="n/a",
            reason=(
                "no UI tree and no UVC signal configured; falling back to the physical"
                " camera with the relative pointer (slowest, needs its own calibration)"
            ),
        )

    raise CompositionError(
        "no usable perception for this device: it has no UI tree and neither a UVC"
        " capture card nor a camera is configured (set the UVC device, or a camera)"
    )
