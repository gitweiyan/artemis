"""The UVC capture card as a perception source.

The one thing this file exists to pin down is the **device selection**: macOS's
AVFoundation device order is not stable. Two listing taken minutes apart put the
capture card at index 1 in one and at index 0 in the next, so ``-i 1`` pointed at the
capture card once and at the built-in camera the next time — and the loop silently ran
on a webcam image of the room instead of the phone. The device is therefore named, and
never addressed by index.

No hardware is needed: the ffmpeg call is a seam.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from artemis.reflex.capture import UvcCapture
from artemis.reflex.contract import CaptureError


class FakeRunner:
    """Stands in for ``subprocess.run``: records the argv, returns canned output."""

    def __init__(
        self, stdout: bytes = b"\x89PNG\r\n\x1a\nDATA", returncode: int = 0, stderr: bytes = b""
    ):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr
        self.commands: list[list[str]] = []

    def __call__(self, cmd, timeout_s):
        self.commands.append(list(cmd))
        return SimpleNamespace(returncode=self.returncode, stdout=self.stdout, stderr=self.stderr)


def test_the_device_is_named_never_addressed_by_index():
    runner = FakeRunner()
    UvcCapture("USB Video", runner=runner).grab()

    cmd = runner.commands[0]
    assert "-i" in cmd
    device = cmd[cmd.index("-i") + 1]
    assert device == "USB Video", "the name, not a drifting index"
    assert not device.isdigit(), "an index would feed the loop whatever the camera order is today"


def test_a_frame_carries_the_image_bytes_and_the_size():
    runner = FakeRunner(stdout=b"\x89PNG-frame")
    frame = UvcCapture("USB Video", size=(1920, 1080), runner=runner).grab()

    assert frame.image == b"\x89PNG-frame"
    assert (frame.width, frame.height) == (1920, 1080)
    assert frame.capture_ms >= 0
    assert frame.captured_at > 0


def test_the_command_asks_avfoundation_for_one_png_frame():
    runner = FakeRunner()
    UvcCapture("USB Video", size=(640, 480), framerate=30, runner=runner).grab()

    cmd = " ".join(runner.commands[0])
    assert "avfoundation" in cmd
    assert "-frames:v 1" in cmd
    assert "640x480" in cmd
    assert "png" in cmd


def test_a_device_that_is_not_there_is_a_capture_error():
    runner = FakeRunner(stdout=b"", returncode=1, stderr=b"Video device not found")
    with pytest.raises(CaptureError):
        UvcCapture("USB Video", runner=runner).grab()


def test_a_transport_failure_is_a_capture_error():
    def boom(cmd, timeout_s):
        raise OSError("ffmpeg is not installed")

    with pytest.raises(CaptureError):
        UvcCapture("USB Video", runner=boom).grab()


def test_an_empty_frame_is_a_capture_error():
    runner = FakeRunner(stdout=b"", returncode=0)
    with pytest.raises(CaptureError):
        UvcCapture("USB Video", runner=runner).grab()


def test_the_crop_removes_the_letterbox_bars():
    """A portrait phone in a landscape frame has black bars; a crop keeps only the screen."""
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (100, 50), (0, 0, 0)).save(buffer, "PNG")
    runner = FakeRunner(stdout=buffer.getvalue())
    capture = UvcCapture("USB Video", size=(100, 50), crop=(20, 0, 80, 50), runner=runner)

    frame = capture.grab()

    assert (frame.width, frame.height) == (60, 50), "the cropped size is what a skill sees"
    assert Image.open(BytesIO(frame.image)).size == (60, 50)


def test_close_is_a_no_op():
    assert UvcCapture("USB Video", runner=FakeRunner()).close() is None


def test_the_command_selects_the_uvc_supported_pixel_format():
    """This card rejects ffmpeg's default yuv420p; explicit nv12 avoids a hung open."""
    runner = FakeRunner()
    UvcCapture("USB Video", pixel_format="nv12", runner=runner).grab()

    cmd = runner.commands[0]
    assert cmd[cmd.index("-pixel_format") + 1] == "nv12"


def test_nv12_is_the_default_pixel_format():
    runner = FakeRunner()
    UvcCapture("USB Video", runner=runner).grab()

    cmd = runner.commands[0]
    assert cmd[cmd.index("-pixel_format") + 1] == "nv12"
