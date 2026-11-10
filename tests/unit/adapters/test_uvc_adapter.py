"""UVC adapters moved to L4 while the legacy import path stays valid."""

from __future__ import annotations

from artemis.adapters import uvc
from artemis.reflex import capture


def test_the_legacy_capture_module_re_exports_the_adapter_classes():
    assert capture.UvcCapture is uvc.UvcCapture
    assert capture.UvcStream is uvc.UvcStream
    assert capture.DEFAULT_UVC_DEVICE == uvc.DEFAULT_UVC_DEVICE


def test_the_adapter_owns_the_device_capture_configuration():
    instance = uvc.UvcCapture("USB Video", size=(640, 480), framerate=15, timeout_s=3.0)

    assert instance.device == "USB Video"
    assert instance.size == (640, 480)
    assert instance.framerate == 15
    assert instance.timeout_s == 3.0
