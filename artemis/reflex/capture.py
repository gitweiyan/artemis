"""Frames straight off the device, with no database in the way.

Two paths, deliberately:

* **helper** (default): the accessibility helper's ``/snapshot`` endpoint over the
  existing ``adb forward`` returns a hardware bitmap + tree in one loopback call —
  the lowest-latency path available without changing the APK;
* **screencap**: ``adb exec-out screencap -p``, slower (a process spawn per frame)
  but always available, and the fallback when the helper is not attached.

Neither writes anything: a reflex session's frames are memory, not artifacts.
Video, if wanted, is the existing recording path's job — not the inner loop's.
"""

from __future__ import annotations

from typing import Any

import base64
import subprocess
import time

from artemis.reflex.attach import AttachTimeout, attach_client
from artemis.reflex.contract import Capture, CaptureError, Frame
from third_party.mobile_use.utils.logger import get_logger

logger = get_logger(__name__)


#: Transport failures worth turning into "no frame"/"no gesture": ``TimeoutExpired``
#: is a ``SubprocessError``, not an ``OSError`` — the same "slow is not the same as
#: absent" lesson that cost a real run 59.8 s elsewhere in this repository.
_ATTACH_ERRORS = (
    RuntimeError,
    OSError,
    subprocess.SubprocessError,
    ValueError,
    KeyError,
    AttributeError,
    ImportError,
)


class HelperCapture(Capture):
    """Screenshot via the accessibility helper's loopback HTTP endpoint."""

    def __init__(
        self, serial: str, *, client: object | None = None, attach_deadline_s: float | None = None
    ):
        self.serial = serial
        self._client: Any = client
        self._owns_client = client is None
        self.attach_deadline_s = attach_deadline_s

    def _ensure_client(self):
        if self._client is None:
            self._client = attach_client(
                self.serial,
                deadline_s=self.attach_deadline_s or 20.0,
            )
        return self._client

    def grab(self) -> Frame:
        try:
            client = self._ensure_client()
        except _ATTACH_ERRORS + (AttachTimeout,) as exc:
            raise CaptureError(
                f"the accessibility helper is not usable ({exc}). Enable it on the device"
                " (Settings > Accessibility > Installed apps > Artemis Accessibility Helper),"
                " or run with capture='screencap'."
            ) from exc
        started = time.monotonic()
        # The helper's own atomic snapshot is the point of this path: one loopback
        # call returning a JPEG the device encoded. ``get_screenshot_base64`` looks
        # like the obvious call and is the wrong one — it shells out to
        # ``adb exec-out screencap -p`` and re-encodes with PIL, which measured
        # 1347 ms against 440 ms for the snapshot (2026-10-10, PEMM00).
        payload = None
        width: int | None = None
        height: int | None = None
        try:
            snapshot = client.get_atomic_snapshot()
        except _ATTACH_ERRORS as exc:
            logger.debug(f"atomic snapshot unavailable, falling back to screencap: {exc}")
            snapshot = None
        if isinstance(snapshot, dict):
            payload = snapshot.get("screenshot_base64")
            raw_w, raw_h = snapshot.get("width"), snapshot.get("height")
            width = int(raw_w) if isinstance(raw_w, (int, float)) else None
            height = int(raw_h) if isinstance(raw_h, (int, float)) else None
        if not payload:
            try:
                payload = client.get_screenshot_base64()
            except _ATTACH_ERRORS as exc:
                raise CaptureError(f"helper screenshot failed: {exc}") from exc
        if not payload:
            raise CaptureError("helper returned no screenshot")
        try:
            image = base64.b64decode(payload)
        except (ValueError, TypeError) as exc:
            raise CaptureError(f"helper screenshot is not base64: {exc}") from exc
        if width is None or height is None:
            info = getattr(client, "screen_size", None)
            if isinstance(info, tuple) and len(info) == 2:
                width, height = int(info[0]), int(info[1])
        return Frame(
            image=image,
            captured_at=time.monotonic(),
            capture_ms=(time.monotonic() - started) * 1000.0,
            width=width,
            height=height,
        )

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            try:
                self._client.disconnect()
            finally:
                self._client = None


class ScreencapCapture(Capture):
    """Screenshot via ``adb exec-out screencap -p`` (PNG)."""

    def __init__(self, serial: str, *, timeout_s: float = 20.0):
        self.serial = serial
        self.timeout_s = timeout_s

    def grab(self) -> Frame:
        started = time.monotonic()
        try:
            result = subprocess.run(
                ["adb", "-s", self.serial, "exec-out", "screencap", "-p"],
                capture_output=True,
                timeout=self.timeout_s,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise CaptureError(f"screencap failed: {exc}") from exc
        if result.returncode != 0 or not result.stdout:
            raise CaptureError(f"screencap returned {result.returncode}: {result.stderr[:200]!r}")
        return Frame(
            image=result.stdout,
            captured_at=time.monotonic(),
            capture_ms=(time.monotonic() - started) * 1000.0,
        )

    def close(self) -> None:
        return None


#: The capture card's name on this Mac (measured 2026-10-16: "USB Video"). A UVC card is
#: chosen **by name, never by index**: macOS's AVFoundation order is not stable — two
#: listings a minute apart put the card at index 1 and then 0, so ``-i 1`` returned the
#: capture card once and the built-in camera the next time, silently feeding the loop a
#: webcam image of the room instead of the phone.
DEFAULT_UVC_DEVICE = "USB Video"
#: The card's frame. 1080p keeps a phone's UI text readable after the letterbox is cropped.
DEFAULT_UVC_SIZE = (1920, 1080)
DEFAULT_UVC_FRAMERATE = 30


def _crop_png(png: bytes, box) -> tuple[bytes, tuple[int, int]]:
    """Crop a PNG to ``box`` (left, top, right, bottom in source pixels) and re-encode."""
    from io import BytesIO

    from PIL import Image

    cropped = Image.open(BytesIO(png)).crop(tuple(box))
    out = BytesIO()
    cropped.save(out, "PNG")
    return out.getvalue(), cropped.size


class UvcCapture(Capture):
    """Frames from a UVC capture card (an HDMI dongle), via ffmpeg + AVFoundation.

    One ffmpeg process per frame, a PNG on stdout. ``crop`` trims a letterboxed phone
    out of the card's wider frame; the frame then reports the *cropped* size, so a
    skill's ROI ratios line up with what it is looking at.
    """

    def __init__(
        self,
        device: str = DEFAULT_UVC_DEVICE,
        *,
        size: tuple[int, int] = DEFAULT_UVC_SIZE,
        framerate: int = DEFAULT_UVC_FRAMERATE,
        crop: tuple[int, int, int, int] | None = None,
        ffmpeg: str = "ffmpeg",
        timeout_s: float = 20.0,
        runner=None,
    ):
        self.device = device
        self.size = (int(size[0]), int(size[1]))
        self.framerate = int(framerate)
        self.crop = tuple(int(v) for v in crop) if crop else None
        self.ffmpeg = ffmpeg
        self.timeout_s = float(timeout_s)
        #: A seam for tests (and for another grabber): ``runner(cmd, timeout_s)``.
        self._runner = runner

    def _command(self) -> list[str]:
        width, height = self.size
        return [
            self.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "avfoundation",
            "-framerate",
            str(self.framerate),
            "-video_size",
            f"{width}x{height}",
            "-i",
            self.device,
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "-",
        ]

    def _run(self, cmd):
        if self._runner is not None:
            return self._runner(cmd, self.timeout_s)
        return subprocess.run(cmd, capture_output=True, timeout=self.timeout_s)

    def grab(self) -> Frame:
        started = time.monotonic()
        try:
            result = self._run(self._command())
        except (OSError, subprocess.SubprocessError) as exc:
            raise CaptureError(f"uvc capture failed: {exc}") from exc
        png = result.stdout
        if result.returncode != 0 or not png:
            raise CaptureError(
                f"uvc capture returned {result.returncode}: {bytes(result.stderr)[:200]!r}"
            )
        if self.crop is not None:
            png, size = _crop_png(png, self.crop)
        else:
            size = self.size
        return Frame(
            image=png,
            captured_at=time.monotonic(),
            capture_ms=(time.monotonic() - started) * 1000.0,
            width=size[0],
            height=size[1],
        )

    def close(self) -> None:
        return None


def default_capture(serial: str, *, prefer: str = "helper") -> Capture:
    """The capture path for ``serial``.

    ``prefer='screencap'`` forces the slow adb path; ``prefer='uvc'`` uses the capture
    card and treats ``serial`` as its **device name** (a UVC card has no adb serial).
    """
    if prefer == "uvc":
        return UvcCapture(serial)
    if prefer == "screencap":
        return ScreencapCapture(serial)
    return HelperCapture(serial)


__all__ = ["HelperCapture", "ScreencapCapture", "UvcCapture", "default_capture"]
