"""P1: a persistent UVC stream instead of one ffmpeg process per frame.

The per-frame path costs ~0.85 s; the pointer fades before the capture finishes.
The stream reads a single ffmpeg's MJPEG stdout, keeps the latest frame, and
splits JPEGs even when they arrive in arbitrary chunks.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from artemis.reflex.capture import UvcStream
from artemis.reflex.contract import CaptureError


def _jpeg(color: tuple[int, int, int], size=(50, 100)) -> bytes:
    out = BytesIO()
    Image.new("RGB", size, color).save(out, "JPEG")
    return out.getvalue()


class FakeStdout:
    def __init__(self, data: bytes, chunk_size: int = 7):
        self.data = data
        self.pos = 0
        self.chunk_size = chunk_size

    def read(self, size: int = -1) -> bytes:
        if self.pos >= len(self.data):
            return b""
        end = min(len(self.data), self.pos + self.chunk_size)
        chunk = self.data[self.pos : end]
        self.pos = end
        return chunk


class FakeProcess:
    def __init__(self, data: bytes):
        self.stdout = FakeStdout(data)
        self.terminated = False
        self.returncode = None
        self.command: list[str] | None = None

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return self.returncode


def test_uvc_stream_splits_frames_and_builds_the_crop_filter():
    first = _jpeg((220, 30, 30))
    second = _jpeg((30, 30, 220))
    process = FakeProcess(first + second)

    def factory(cmd):
        process.command = cmd
        return process

    stream = UvcStream(
        "USB Video",
        size=(100, 200),
        crop=(10, 20, 60, 120),
        process_factory=factory,
    )

    frame_a = stream.grab(timeout_s=1.0)
    frame_b = stream.grab(timeout_s=1.0)

    assert frame_a.image == first
    assert frame_b.image == second
    assert (frame_a.width, frame_a.height) == (50, 100)
    assert (frame_b.width, frame_b.height) == (50, 100)
    assert frame_a.capture_ms >= 0.0
    assert "crop=50:100:10:20" in " ".join(process.command or [])
    stream.close()
    assert process.terminated is True


def test_uvc_stream_assembles_frames_split_across_reads():
    data = _jpeg((10, 200, 10))
    process = FakeProcess(data)
    stream = UvcStream(
        "USB Video",
        size=(50, 100),
        process_factory=lambda cmd: process,
    )

    frame = stream.grab(timeout_s=1.0)

    assert frame.image == data
    stream.close()


def test_uvc_stream_reports_a_timeout_when_no_frame_arrives():
    process = FakeProcess(b"")
    stream = UvcStream(
        "USB Video",
        size=(50, 100),
        process_factory=lambda cmd: process,
    )

    with pytest.raises(CaptureError):
        stream.grab(timeout_s=0.05)

    stream.close()


def test_default_capture_can_select_the_persistent_uvc_stream():
    from artemis.reflex.capture import default_capture

    capture = default_capture("USB Video", prefer="uvc-stream")

    assert isinstance(capture, UvcStream)


def test_grab_latest_discards_queued_frames_and_returns_the_newest():
    first = _jpeg((220, 30, 30))
    second = _jpeg((30, 30, 220))
    process = FakeProcess(first + second)
    stream = UvcStream(
        "USB Video",
        size=(50, 100),
        process_factory=lambda cmd: process,
    )

    frame = stream.grab_latest(timeout_s=1.0)

    assert frame.image == second
    stream.close()
