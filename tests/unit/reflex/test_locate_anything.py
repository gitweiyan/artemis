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


def test_a_warm_up_primes_the_service_with_a_budget_of_its_own(monkeypatch):
    """The first inference after an idle period loads the model (measured 3.6-25.5 s).

    A warm-up exists so the cold load does not happen inside a click's budget, so it
    needs a longer timeout than the click path and must never raise.
    """
    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append((json.loads(request.data), timeout))
        return _Response({"point_norm": [500, 500]})

    monkeypatch.setattr("artemis.reflex.locate_anything.urllib.request.urlopen", fake_urlopen)
    detector = LocateAnythingDetector("http://127.0.0.1:7861", timeout_s=10.0)

    assert detector.warm_up() is True
    assert calls, "the warm-up must actually send a request"
    payload, timeout = calls[0]
    assert payload["image"].startswith("data:image/png;base64,")
    assert timeout >= 30.0, "the cold load needs more than a click's budget"
    assert detector._failures == 0 and detector._skip_until == 0.0, "a warm-up is not a failure"


def test_a_failed_warm_up_is_swallowed_not_counted(monkeypatch):
    def boom(request, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr("artemis.reflex.locate_anything.urllib.request.urlopen", boom)
    detector = LocateAnythingDetector("http://127.0.0.1:7861")

    assert detector.warm_up() is False
    assert detector._failures == 0, "warming up must not arm the cooldown"


def test_repeated_failures_back_off_instead_of_disabling_the_run(monkeypatch):
    """Two failures used to skip the service for 60 s - the rest of a short run.

    2026-11-06 15:58: both calls timed out (cold model), then every later click ran on
    unverified model coordinates for the last 54 s of the run.
    """
    now = [1000.0]
    monkeypatch.setattr("artemis.reflex.locate_anything.time.monotonic", lambda: now[0])

    def boom(request, timeout=None):
        raise TimeoutError("timed out")

    monkeypatch.setattr("artemis.reflex.locate_anything.urllib.request.urlopen", boom)
    detector = LocateAnythingDetector(
        "http://127.0.0.1:7861", timeout_s=1.0, cooldown_s=5.0, max_cooldown_s=20.0
    )

    detector.locate(b"png", "a")
    detector.locate(b"png", "b")
    assert detector._skip_until - now[0] == pytest.approx(5.0), "first backoff is the base"
    assert detector.locate(b"png", "cooldown") is None
    assert "cooling" in (detector.last_error or "")

    now[0] += 5.5
    detector.locate(b"png", "c")
    assert detector._skip_until - now[0] == pytest.approx(10.0), "the skip doubles each failure"

    now[0] += 10.5
    detector.locate(b"png", "d")
    assert detector._skip_until - now[0] == pytest.approx(20.0), "and it is capped"

    now[0] += 20.5

    def ok(request, timeout=None):
        return _Response({"point_norm": [10, 20]})

    monkeypatch.setattr("artemis.reflex.locate_anything.urllib.request.urlopen", ok)
    assert detector.locate(b"png", "after the backoff") == (0.01, 0.02)
    assert detector._failures == 0, "a success clears the backoff"
