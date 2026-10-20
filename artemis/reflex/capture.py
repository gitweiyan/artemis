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
import os
import select
import subprocess
import time
from io import BytesIO

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
        pixel_format: str | None = "nv12",
    ):
        self.device = device
        self.size = (int(size[0]), int(size[1]))
        self.framerate = int(framerate)
        self.pixel_format = pixel_format
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
            *(["-pixel_format", self.pixel_format] if self.pixel_format else []),
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


class UvcStream(Capture):
    """Persistent UVC frames from one ffmpeg process (I9/P1).

    The per-frame :class:`UvcCapture` spawns ffmpeg for every grab (~0.85 s); the
    AssistiveTouch pointer fades before the frame arrives. This class keeps one
    MJPEG ffmpeg alive, crops in ffmpeg (no Python re-encode), and splits JPEG
    frames from arbitrary stdout chunks. ``grab()`` returns the next complete
    frame, so a pointer nudge can be followed by a fresh observation quickly.

    ``process_factory(cmd)`` is a test seam; production uses ``subprocess.Popen``.
    """

    _MAX_BUFFER = 8 * 1024 * 1024

    def __init__(
        self,
        device: str = DEFAULT_UVC_DEVICE,
        *,
        size: tuple[int, int] = DEFAULT_UVC_SIZE,
        framerate: int = DEFAULT_UVC_FRAMERATE,
        crop: tuple[int, int, int, int] | None = None,
        ffmpeg: str = "ffmpeg",
        timeout_s: float = 2.0,
        process_factory=None,
        pixel_format: str | None = "nv12",
        drain_budget_s: float = 0.05,
    ):
        self.device = device
        self.size = (int(size[0]), int(size[1]))
        self.framerate = int(framerate)
        self.pixel_format = pixel_format
        self.crop = tuple(int(value) for value in crop) if crop else None
        self.ffmpeg = ffmpeg
        self.timeout_s = float(timeout_s)
        #: How long ``grab_latest`` may drain an already-running stream. A live 30 fps
        #: camera always has more bytes ready, so "drain until empty" never terminates;
        #: this bounds the freshness/discard work and guarantees a return.
        self.drain_budget_s = max(0.0, float(drain_budget_s))
        self._process_factory = process_factory
        self._process = None
        self._buffer = b""
        if self.crop:
            left, top, right, bottom = self.crop
            self.frame_size = (right - left, bottom - top)
        else:
            self.frame_size = self.size

    def _command(self) -> list[str]:
        width, height = self.size
        cmd = [
            self.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "avfoundation",
            *(["-pixel_format", self.pixel_format] if self.pixel_format else []),
            "-framerate",
            str(self.framerate),
            "-video_size",
            f"{width}x{height}",
            "-i",
            self.device,
        ]
        if self.crop:
            left, top, right, bottom = self.crop
            cmd += ["-vf", f"crop={right - left}:{bottom - top}:{left}:{top}"]
        cmd += [
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "-q:v",
            "3",
            "-",
        ]
        return cmd

    def _start(self) -> None:
        if self._process is not None:
            return
        cmd = self._command()
        if self._process_factory is not None:
            self._process = self._process_factory(cmd)
        else:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )

    def _read_chunk(self) -> bytes | None:
        stdout = self._process.stdout
        try:
            fd = stdout.fileno()
        except (AttributeError, OSError, ValueError):
            return stdout.read(4096)
        readable, _, _ = select.select([fd], [], [], 0.05)
        if not readable:
            return None
        return os.read(fd, 4096)

    def _read_chunk_nowait(self) -> bytes | None:
        stdout = self._process.stdout
        try:
            fd = stdout.fileno()
        except (AttributeError, OSError, ValueError):
            return stdout.read(4096)
        readable, _, _ = select.select([fd], [], [], 0)
        if not readable:
            return None
        return os.read(fd, 4096)

    def _make_frame(self, jpeg: bytes, started: float) -> Frame:
        return Frame(
            image=jpeg,
            captured_at=time.monotonic(),
            capture_ms=(time.monotonic() - started) * 1000.0,
            width=self.frame_size[0],
            height=self.frame_size[1],
        )

    def _pop_jpeg(self) -> bytes | None:
        start = self._buffer.find(b"\xff\xd8")
        if start < 0:
            # Keep a trailing 0xFF: it may be the first byte of the next SOI.
            self._buffer = self._buffer[-1:] if self._buffer.endswith(b"\xff") else b""
            return None
        end = self._buffer.find(b"\xff\xd9", start + 2)
        if end < 0:
            return None
        frame = self._buffer[start : end + 2]
        self._buffer = self._buffer[end + 2 :]
        return frame

    def grab(self, timeout_s: float | None = None) -> Frame:
        self._start()
        started = time.monotonic()
        timeout = self.timeout_s if timeout_s is None else float(timeout_s)
        deadline = started + timeout

        while True:
            jpeg = self._pop_jpeg()
            if jpeg is not None:
                return self._make_frame(jpeg, started)
            if time.monotonic() >= deadline:
                raise CaptureError(f"uvc stream produced no frame within {timeout:.2f}s")
            chunk = self._read_chunk()
            if chunk:
                self._buffer += chunk
                if len(self._buffer) > self._MAX_BUFFER:
                    self._buffer = b""
                    raise CaptureError("uvc stream buffer exceeded its cap without a frame")
            elif chunk == b"":
                raise CaptureError("uvc stream ended before a frame arrived")

    def grab_latest(self, timeout_s: float | None = None) -> Frame:
        """Return the newest frame currently available (drain older queued frames).

        ffmpeg keeps producing while the caller is planning or moving the pointer;
        returning the oldest queued frame would feed the controller a stale image.
        """
        self._start()
        started = time.monotonic()
        timeout = self.timeout_s if timeout_s is None else float(timeout_s)
        deadline = started + timeout
        drain_deadline = started + min(self.drain_budget_s, timeout)
        newest: bytes | None = None

        while True:
            # Extract frames as chunks arrive. Draining megabytes first and only
            # then splitting can trip the buffer cap while complete frames are
            # already sitting in the buffer.
            chunk = self._read_chunk_nowait()
            if chunk:
                self._buffer += chunk
                while True:
                    jpeg = self._pop_jpeg()
                    if jpeg is None:
                        break
                    newest = jpeg
                if len(self._buffer) > self._MAX_BUFFER and newest is None:
                    self._buffer = b""
                    raise CaptureError("uvc stream buffer exceeded its cap without a frame")
                if newest is not None and time.monotonic() >= drain_deadline:
                    # A live camera keeps producing; return the newest frame seen so
                    # far instead of chasing the stream forever. The backlog (if any)
                    # is consumed first, within the drain budget.
                    return self._make_frame(newest, started)
                continue

            # No complete frame yet; pull a partial frame out of the buffer.
            while True:
                jpeg = self._pop_jpeg()
                if jpeg is None:
                    break
                newest = jpeg
            if newest is not None:
                return self._make_frame(newest, started)
            if time.monotonic() >= deadline:
                raise CaptureError(f"uvc stream produced no frame within {timeout:.2f}s")
            chunk = self._read_chunk()
            if chunk:
                self._buffer += chunk
            elif chunk == b"":
                raise CaptureError("uvc stream ended before a frame arrived")

    def close(self) -> None:
        process = self._process
        self._process = None
        self._buffer = b""
        if process is None:
            return
        try:
            process.terminate()
            process.wait(timeout=2.0)
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            process.stdout.close()
        except (AttributeError, OSError):
            pass


#: macOS screen rectangle of the AirPlay receiver window, ``left,top,right,bottom``.
#: Unset means the whole screen. Only used by ``prefer='airplay'``.
ENV_AIRPLAY_REGION = "ARTEMIS_AIRPLAY_REGION"


def airplay_region() -> tuple[int, int, int, int] | None:
    """The configured AirPlay receiver rectangle, or ``None`` for the whole screen."""
    raw = os.environ.get(ENV_AIRPLAY_REGION)
    if not raw or not raw.strip():
        return None
    try:
        parts = tuple(int(part.strip()) for part in raw.split(","))
    except ValueError as exc:
        raise ValueError(f"{ENV_AIRPLAY_REGION} must be 'left,top,right,bottom'") from exc
    if len(parts) != 4:
        raise ValueError(f"{ENV_AIRPLAY_REGION} must have four integers, got {raw!r}")
    left, top, right, bottom = parts
    if left >= right or top >= bottom:
        raise ValueError(f"{ENV_AIRPLAY_REGION} rectangle is empty: {raw!r}")
    return parts


class AirPlayCapture(Capture):
    """Frames from an AirPlay receiver window on the Mac (iPhone fallback).

    AirPlay mirrors the phone into a window (UxPlay or the macOS AirPlay receiver);
    this adapter grabs that screen region. It needs macOS Screen Recording
    permission. AirPlay adds 1–2s latency, so it is a fallback for tasks that can
    tolerate it, not a high-speed inner loop; UVC + AssistiveTouch remains the
    first choice when the pointer is visible.

    ``grabber(region)`` is a test seam (and a different screen-capture transport):
    it returns a ``PIL.Image`` for the region.
    """

    def __init__(
        self,
        region: tuple[int, int, int, int] | None = None,
        *,
        grabber=None,
    ):
        if region is not None:
            if len(region) != 4:
                raise ValueError("airplay region must be left,top,right,bottom")
            left, top, right, bottom = (int(value) for value in region)
            if left >= right or top >= bottom:
                raise ValueError(f"airplay region is empty: {region!r}")
            self.region: tuple[int, int, int, int] | None = (left, top, right, bottom)
        else:
            self.region = None
        self._grabber = grabber

    def grab(self) -> Frame:
        started = time.monotonic()
        grabber = self._grabber if self._grabber is not None else _grab_screen
        try:
            image = grabber(self.region)
        except (OSError, RuntimeError, ValueError) as exc:
            raise CaptureError(f"airplay capture failed: {exc}") from exc
        if image is None:
            raise CaptureError("airplay capture returned no image")
        out = BytesIO()
        try:
            image.save(out, "PNG")
        except (OSError, ValueError) as exc:
            raise CaptureError(f"airplay capture could not encode the frame: {exc}") from exc
        return Frame(
            image=out.getvalue(),
            captured_at=time.monotonic(),
            capture_ms=(time.monotonic() - started) * 1000.0,
            width=image.width,
            height=image.height,
        )

    def close(self) -> None:
        return None


def _grab_screen(region: tuple[int, int, int, int] | None):
    from PIL import ImageGrab

    return ImageGrab.grab(bbox=region)


def default_capture(serial: str, *, prefer: str = "helper") -> Capture:
    """The capture path for ``serial``.

    ``prefer='screencap'`` forces the slow adb path; ``prefer='uvc'`` uses the capture
    card and treats ``serial`` as its **device name** (a UVC card has no adb serial).
    ``prefer='airplay'`` grabs the macOS AirPlay receiver window region configured by
    ``ARTEMIS_AIRPLAY_REGION`` (the slow iPhone fallback; UVC remains first choice).
    ``prefer='uvc-stream'`` keeps one ffmpeg process alive for low-latency frames.
    """
    if prefer in ("uvc", "uvc-stream"):
        return UvcCapture(serial) if prefer == "uvc" else UvcStream(serial)
    if prefer == "airplay":
        return AirPlayCapture(airplay_region())
    if prefer == "screencap":
        return ScreencapCapture(serial)
    return HelperCapture(serial)


__all__ = [
    "ENV_AIRPLAY_REGION",
    "AirPlayCapture",
    "HelperCapture",
    "ScreencapCapture",
    "UvcCapture",
    "UvcStream",
    "airplay_region",
    "default_capture",
]
