from io import BytesIO
from types import SimpleNamespace

import asyncio
from PIL import Image

from artemis.devices.ios_uvc import build_ios_uvc_session


class Capture:
    def grab(self):
        buffer = BytesIO()
        Image.new("RGB", (10, 10), (200, 200, 200)).save(buffer, "PNG")
        return SimpleNamespace(image=buffer.getvalue(), width=10, height=10, captured_at=1.0)


class Control:
    def preflight(self):
        return {}

    def mouse_home(self):
        pass

    def mouse_move(self, dx, dy):
        pass

    def mouse_click(self):
        pass


def test_builds_a_standard_session_with_uvc_and_hid_capabilities():
    session = build_ios_uvc_session(Capture(), Control())
    assert session.capabilities.supports_relative_pointer is True
    assert session.capabilities.requires_pointer_precheck is True
    assert session.capabilities.supports_ui_tree is False
    assert asyncio.run(session.snapshot()).health == "ok"


class OcrEngine:
    def recognize(self, image, *, width, height):
        from artemis.perception.base import TextBox

        return [TextBox("闪购", (0.3, 0.1, 0.4, 0.2), 0.99)]


def test_ocr_engine_is_available_through_ios_session(monkeypatch):
    from artemis.perception.base import Target

    monkeypatch.setenv("ARTEMIS_IOS_LOCATE_URL", "off")
    session = build_ios_uvc_session(Capture(), Control(), ocr_engine=OcrEngine())
    source = session.perception[0]
    frame = asyncio.run(session.snapshot())
    target = asyncio.run(source.locate(frame, Target("闪购", (0, 0, 0, 0), 0, "ocr")))

    assert target is not None
    assert target.box == (0.3, 0.1, 0.4, 0.2)


def test_tap_target_maps_box_center_to_relative_hid_sink():
    import asyncio
    from artemis.control.base import ActionResult
    from artemis.devices.geometry import ScreenGeometry
    from artemis.devices.ios_uvc import tap_target
    from artemis.devices.session import DeviceSession
    from artemis.devices.capabilities import CapabilitySet
    from artemis.perception.base import Target

    class Sink:
        capabilities = CapabilitySet(supports_relative_pointer=True)

        def __init__(self):
            self.calls = []

        async def tap_abs(self, x, y):
            self.calls.append((x, y))
            return ActionResult(True, "tap_abs")

    sink = Sink()
    session = DeviceSession([], [sink], ScreenGeometry((449, 974), (1080, 2400)))
    target = Target("闪购", (0.296, 0.0719, 0.394, 0.0955), 0.99, "ocr")

    result = asyncio.run(tap_target(session, target))

    assert result.ok is True
    assert sink.calls == [(373, 201)]


def test_the_pointer_adapter_prefers_grab_latest_when_the_capture_has_it():
    from artemis.devices.ios_uvc import _GrabLatest

    class Streaming:
        def __init__(self):
            self.latest = 0
            self.plain = 0

        def grab(self):
            self.plain += 1
            return "plain"

        def grab_latest(self):
            self.latest += 1
            return "latest"

    capture = Streaming()
    assert _GrabLatest(capture).grab_latest() == "latest"
    assert (capture.latest, capture.plain) == (1, 0)


def test_the_session_exposes_the_pointer_belief_the_driver_anchors():
    import asyncio

    from artemis.drivers.ios.ios_uvc_hid_driver import IOSUvcHidDriver

    session = build_ios_uvc_session(Capture(), Control())
    driver = IOSUvcHidDriver(session)

    assert session.pointer is not None
    assert session.pointer.connected is False

    asyncio.run(driver.connect())

    assert session.pointer.connected is True
    assert session.pointer.position == (0.0, 0.0), "connect re-anchors the pointer"


def test_the_session_backs_the_colour_detector_with_the_nudge_diff():
    """A grey or edge-clipped pointer escapes colour/shape detection; the diff path
    needs no appearance model, so the session always carries it."""
    session = build_ios_uvc_session(Capture(), Control())
    sink = session.control[0]

    assert sink.controller.tracker is not None
    assert sink.controller.tracker.has_template is False, "no template to maintain"


def test_the_assistive_button_box_is_forwarded_to_the_tracker():
    session = build_ios_uvc_session(Capture(), Control(), assistive_button_box=(380, 200, 449, 280))
    assert session.control[0].controller.tracker.menu_box == (380, 200, 449, 280)


def test_the_ios_session_trusts_only_the_blue_ring(monkeypatch):
    """The legacy red detector must not drive an iOS pointer (bench: red UI badges)."""
    from artemis.reflex.vision import BluePointerDetector

    monkeypatch.setenv("ARTEMIS_IOS_LOCATE_URL", "off")
    session = build_ios_uvc_session(Capture(), Control())
    detector = session.control[0].controller.detector

    assert [type(item) for item in detector.detectors] == [BluePointerDetector]


def test_the_locate_anything_service_is_the_first_pointer_locator(monkeypatch):
    from artemis.reflex.locate_anything import LocateAnythingDetector
    from artemis.reflex.vision import BluePointerDetector

    monkeypatch.setenv("ARTEMIS_IOS_LOCATE_URL", "http://127.0.0.1:7862")
    session = build_ios_uvc_session(Capture(), Control())
    detectors = session.control[0].controller.detector.detectors

    assert [type(item) for item in detectors] == [LocateAnythingDetector, BluePointerDetector]


def test_the_locate_service_is_the_first_target_locator(monkeypatch):
    from artemis.perception.base import Target
    from artemis.reflex.locate_anything import LocateAnythingDetector

    monkeypatch.setenv("ARTEMIS_IOS_LOCATE_URL", "http://127.0.0.1:7862")
    monkeypatch.setattr(LocateAnythingDetector, "locate", lambda self, image, prompt: (0.5, 0.5))
    session = build_ios_uvc_session(Capture(), Control())
    source = session.perception[0]
    frame = asyncio.run(session.snapshot())

    target = asyncio.run(source.locate(frame, Target("关闭红包的X", (0, 0, 0, 0), 0, "ocr")))

    assert target is not None
    assert target.source == "locate-anything"
    assert target.box == (0.495, 0.495, 0.505, 0.505)
