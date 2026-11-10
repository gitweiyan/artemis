"""UVC capture adapters: one-shot ffmpeg grabs and a persistent MJPEG stream.

Moved out of ``artemis.reflex.capture`` in Phase 4.1 (ADR-005/006); the legacy
module re-exports these classes so existing skill/diagnostic imports keep working.
The classes still implement the lightweight Reflex ``Capture`` protocol.
"""

from __future__ import annotations

import os
import select
import subprocess
import time

from artemis.reflex.contract import Capture, CaptureError, Frame
from third_party.mobile_use.utils.logger import get_logger

logger = get_logger(__name__)

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
        except subprocess.TimeoutExpired as exc:
            raise CaptureError(f"uvc capture timed out: {exc}", incident="capture_timeout") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise CaptureError(f"uvc capture failed: {exc}", incident="transport_error") from exc
        png = result.stdout
        if result.returncode != 0 or not png:
            raise CaptureError(
                f"uvc capture returned {result.returncode}: {bytes(result.stderr)[:200]!r}",
                incident="uvc_no_signal",
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
                raise CaptureError(
                    f"uvc stream produced no frame within {timeout:.2f}s",
                    incident="capture_timeout",
                )
            chunk = self._read_chunk()
            if chunk:
                self._buffer += chunk
                if len(self._buffer) > self._MAX_BUFFER:
                    self._buffer = b""
                    raise CaptureError(
                        "uvc stream buffer exceeded its cap without a frame",
                        incident="decode_error",
                    )
            elif chunk == b"":
                raise CaptureError(
                    "uvc stream ended before a frame arrived",
                    incident="device_disconnected",
                )

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
                    raise CaptureError(
                        "uvc stream buffer exceeded its cap without a frame",
                        incident="decode_error",
                    )
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
                raise CaptureError(
                    f"uvc stream produced no frame within {timeout:.2f}s",
                    incident="capture_timeout",
                )
            chunk = self._read_chunk()
            if chunk:
                self._buffer += chunk
            elif chunk == b"":
                raise CaptureError(
                    "uvc stream ended before a frame arrived",
                    incident="device_disconnected",
                )

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
