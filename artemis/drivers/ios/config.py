"""The iOS UVC + ESP32 HID rig, described by validated configuration.

One place turns environment variables into a session description, so a run either
starts from a coherent rig or fails at configuration time naming the setting that is
wrong. The defaults are the measured rig (2026-10-19/20): an HDMI capture card at
1920x1080 with the phone's picture at ``736,53,1185,1027`` (a 449x974 frame), an
iPhone 14 Pro Max at 1080x2400, and a pointer gain of 1.8 device pixels per HID unit.

No hardware is touched here: this module is importable and testable on any machine.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping

__all__ = [
    "DEFAULT_UVC_CROP",
    "IosConfigError",
    "IosRuntimeConfig",
    "ios_device_context",
]

#: The phone's rectangle inside the capture card's 1920x1080 letterboxed frame.
DEFAULT_UVC_CROP = (736, 53, 1185, 1027)
#: The card's frame; the phone's picture is cropped out of it.
DEFAULT_UVC_SIZE = (1920, 1080)
#: iPhone 14 Pro Max logical pixels, the rig's device frame.
DEFAULT_SCREEN_SIZE = (1080, 2400)
#: Device pixels travelled per HID pointer unit. Measured on the live rig with
#: ``artemis ios-calibrate`` (2026-10-21): 1.48 for small moves, 1.78 for large ones
#: (acceleration ×1.20). The tap loop estimates the ratio online, so this is only the
#: starting value; the measured number costs fewer correction steps.
DEFAULT_POINTER_GAIN = (1.48, 1.48)

#: The ESP32-S3 board's UART port. The only required setting.
ENV_ESP32_PORT = "ARTEMIS_ESP32_PORT"
ENV_UVC_DEVICE = "ARTEMIS_UVC_DEVICE"
ENV_UVC_CROP = "ARTEMIS_UVC_CROP"
ENV_SCREEN_SIZE = "ARTEMIS_IOS_SCREEN_SIZE"
ENV_POINTER_GAIN = "ARTEMIS_IOS_POINTER_GAIN"
ENV_OCR = "ARTEMIS_IOS_OCR"
ENV_CAPTURE = "ARTEMIS_IOS_CAPTURE"


class IosConfigError(RuntimeError):
    """The iOS rig is not configured well enough to build a session."""


def _env(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _ints(env: Mapping[str, str], name: str, count: int) -> tuple[int, ...] | None:
    raw = _env(env, name)
    if raw is None:
        return None
    try:
        parts = tuple(int(part.strip()) for part in raw.split(","))
    except ValueError as exc:
        raise IosConfigError(
            f"{name} must be {count} comma-separated integers, got {raw!r}"
        ) from exc
    if len(parts) != count:
        raise IosConfigError(f"{name} must be {count} comma-separated integers, got {raw!r}")
    return parts


def _size(env: Mapping[str, str], name: str) -> tuple[int, int] | None:
    raw = _env(env, name)
    if raw is None:
        return None
    for separator in ("x", "X", ","):
        if separator in raw:
            left, _, right = raw.partition(separator)
            break
    else:
        raise IosConfigError(f"{name} must be 'WIDTHxHEIGHT', got {raw!r}")
    try:
        width, height = int(left), int(right)
    except ValueError as exc:
        raise IosConfigError(f"{name} must be 'WIDTHxHEIGHT', got {raw!r}") from exc
    if width <= 0 or height <= 0:
        raise IosConfigError(f"{name} must be positive, got {raw!r}")
    return width, height


def _floats(env: Mapping[str, str], name: str) -> tuple[float, float] | None:
    raw = _env(env, name)
    if raw is None:
        return None
    try:
        parts = tuple(float(part.strip()) for part in raw.split(","))
    except ValueError as exc:
        raise IosConfigError(f"{name} must be 'x,y', got {raw!r}") from exc
    if len(parts) != 2 or any(part <= 0 for part in parts):
        raise IosConfigError(f"{name} must be two positive numbers, got {raw!r}")
    return parts


def _flag(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = _env(env, name)
    if raw is None:
        return default
    lowered = raw.lower()
    if lowered in ("1", "true", "yes", "on"):
        return True
    if lowered in ("0", "false", "no", "off"):
        return False
    raise IosConfigError(f"{name} must be 0 or 1, got {raw!r}")


def _crop(env: Mapping[str, str]) -> tuple[int, int, int, int]:
    raw = _ints(env, ENV_UVC_CROP, 4)
    if raw is None:
        return DEFAULT_UVC_CROP
    left, top, right, bottom = raw
    if left < 0 or top < 0 or left >= right or top >= bottom:
        raise IosConfigError(
            f"{ENV_UVC_CROP} must be 'left,top,right,bottom' with left<right and"
            f" top<bottom, got {raw!r}"
        )
    return left, top, right, bottom


@dataclass(frozen=True)
class IosRuntimeConfig:
    """Everything one iOS UVC + HID session needs, validated."""

    esp32_port: str
    uvc_device: str = "USB Video"
    uvc_size: tuple[int, int] = DEFAULT_UVC_SIZE
    uvc_crop: tuple[int, int, int, int] = DEFAULT_UVC_CROP
    screen_size: tuple[int, int] = DEFAULT_SCREEN_SIZE
    pointer_gain: tuple[float, float] = DEFAULT_POINTER_GAIN
    enable_ocr: bool = True
    #: ``frame`` spawns one ffmpeg per grab (~0.9 s, always a live frame). ``stream``
    #: keeps one ffmpeg alive (faster when it works), but on this rig a persistent
    #: AVFoundation session returned the *same* frame forever while one-shot grabs
    #: stayed live (measured 2026-10-21), so per-frame is the honest default.
    capture_mode: str = "frame"
    #: The cropped capture size a skill's coordinates are expressed in.
    frame_size: tuple[int, int] = field(init=False)

    def __post_init__(self) -> None:
        left, top, right, bottom = self.uvc_crop
        object.__setattr__(self, "frame_size", (right - left, bottom - top))

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> IosRuntimeConfig:
        """Reads the rig description from ``env`` (defaults to ``os.environ``)."""
        env = os.environ if env is None else env
        port = _env(env, ENV_ESP32_PORT)
        if not port:
            raise IosConfigError(
                f"iOS UVC + HID runs need {ENV_ESP32_PORT} (the ESP32-S3 board's serial"
                " port, e.g. /dev/tty.usbmodem1101). Without it Artemis would silently"
                " fall back to Android ADB, which cannot drive this rig."
            )
        gain = _floats(env, ENV_POINTER_GAIN) or DEFAULT_POINTER_GAIN
        mode = (_env(env, ENV_CAPTURE) or "frame").lower()
        if mode not in ("stream", "frame"):
            raise IosConfigError(f"{ENV_CAPTURE} must be 'stream' or 'frame', got {mode!r}")
        return cls(
            esp32_port=port,
            uvc_device=_env(env, ENV_UVC_DEVICE) or "USB Video",
            uvc_crop=_crop(env),
            screen_size=_size(env, ENV_SCREEN_SIZE) or DEFAULT_SCREEN_SIZE,
            pointer_gain=gain,
            enable_ocr=_flag(env, ENV_OCR, True),
            capture_mode=mode,
        )

    def describe(self) -> str:
        """A one-line summary for startup logs (no secrets: the port path is local)."""
        crop = ",".join(str(value) for value in self.uvc_crop)
        gain = ",".join(f"{value:g}" for value in self.pointer_gain)
        return (
            f"iOS UVC+HID port={self.esp32_port} device={self.uvc_device!r}"
            f" crop={crop} frame={self.frame_size[0]}x{self.frame_size[1]}"
            f" screen={self.screen_size[0]}x{self.screen_size[1]} gain={gain}"
            f" ocr={'on' if self.enable_ocr else 'off'} capture={self.capture_mode}"
        )


def ios_device_context(device_id: str, *, env: Mapping[str, str] | None = None):
    """The ``DeviceContext`` for an iOS run: no ADB, dimensions from the config."""
    from third_party.mobile_use.context import DeviceContext, DevicePlatform

    config = IosRuntimeConfig.from_env(env)
    return DeviceContext(
        mobile_platform=DevicePlatform.IOS,
        device_id=device_id or "ios-uvc",
        device_width=config.screen_size[0],
        device_height=config.screen_size[1],
    )
