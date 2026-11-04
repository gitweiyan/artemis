"""The LocateAnything HTTP detector: first pointer locator, colour chain as fallback."""

from __future__ import annotations

import json

import pytest

from artemis.reflex.locate_anything import LocateAnythingDetector
from artemis.reflex.vision import Pointer


class _Response:
    def __init__(self, body):
        self._body = json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_the_service_point_is_turned_into_a_pointer(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append(json.loads(request.data))
        return _Response({"point_norm": [244, 39]})

    monkeypatch.setattr("artemis.reflex.locate_anything.urllib.request.urlopen", fake_urlopen)
    detector = LocateAnythingDetector("http://127.0.0.1:7861")

    pointer = detector.detect(b"\x89PNG\r\n\x1a\n" + b"x" * 10)

    assert isinstance(pointer, Pointer)
    assert pointer.x == pytest.approx(0.244)
    assert pointer.y == pytest.approx(0.039)
    assert pointer.method == "locate-anything"
    assert calls[0]["prompt"] == "the blue circular mouse pointer"
    assert calls[0]["image"].startswith("data:image/png;base64,")


def test_a_jpeg_frame_is_sent_with_the_jpeg_mime(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append(json.loads(request.data))
        return _Response({"point_norm": [1, 2]})

    monkeypatch.setattr("artemis.reflex.locate_anything.urllib.request.urlopen", fake_urlopen)

    LocateAnythingDetector().detect(b"\xff\xd8\xff" + b"x" * 10)

    assert calls[0]["image"].startswith("data:image/jpeg;base64,")


def test_a_transport_failure_returns_none_and_the_next_attempts_are_skipped(monkeypatch):
    calls = []

    def failing_urlopen(request, timeout=None):
        calls.append(1)
        raise OSError("connection refused")

    monkeypatch.setattr("artemis.reflex.locate_anything.urllib.request.urlopen", failing_urlopen)
    detector = LocateAnythingDetector(cooldown_s=60.0)

    assert detector.detect(b"png") is None
    assert detector.detect(b"png") is None
    assert detector.detect(b"png") is None, "inside the cooldown: no HTTP call"

    assert len(calls) == 2
