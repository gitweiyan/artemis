"""I2: AirPlay is the iPhone fallback when UVC cannot show the pointer.

The receiver (UxPlay or the macOS AirPlay receiver) is a window on the Mac; the
capture adapter grabs that screen region. These tests use a grabber seam, so no
receiver or screen-recording permission is needed.
"""

from __future__ import annotations

from PIL import Image

from artemis.reflex.capture import AirPlayCapture, airplay_region, default_capture


def test_airplay_capture_grabs_the_configured_region():
    grabbed: list[tuple[int, int, int, int] | None] = []

    def grabber(region):
        grabbed.append(region)
        return Image.new("RGB", (120, 80), (30, 60, 90))

    capture = AirPlayCapture((10, 20, 130, 100), grabber=grabber)

    frame = capture.grab()

    assert grabbed == [(10, 20, 130, 100)]
    assert frame.width == 120 and frame.height == 80
    assert frame.image.startswith(b"\x89PNG\r\n\x1a\n")
    assert frame.capture_ms >= 0.0
    assert capture.close() is None


def test_airplay_capture_without_a_region_grabs_the_whole_screen():
    seen: list[tuple[int, int, int, int] | None] = []

    def grabber(region):
        seen.append(region)
        return Image.new("RGB", (40, 40), (0, 0, 0))

    AirPlayCapture(None, grabber=grabber).grab()

    assert seen == [None]


def test_the_airplay_region_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("ARTEMIS_AIRPLAY_REGION", "10, 20, 300, 700")

    assert airplay_region() == (10, 20, 300, 700)


def test_an_invalid_airplay_region_is_rejected(monkeypatch):
    import pytest

    monkeypatch.setenv("ARTEMIS_AIRPLAY_REGION", "10,20,5,700")

    with pytest.raises(ValueError):
        airplay_region()


def test_default_capture_can_select_airplay(monkeypatch):
    monkeypatch.setenv("ARTEMIS_AIRPLAY_REGION", "1,2,101,202")

    capture = default_capture("ignored", prefer="airplay")

    assert isinstance(capture, AirPlayCapture)
    assert capture.region == (1, 2, 101, 202)
