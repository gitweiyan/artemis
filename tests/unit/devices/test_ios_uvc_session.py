from io import BytesIO
from types import SimpleNamespace

import asyncio
from PIL import Image

from artemis.devices.ios_uvc import build_ios_uvc_session


class Capture:
    def grab(self):
        buffer = BytesIO()
        Image.new("RGB", (10, 10), (200, 200, 200)).save(buffer, "PNG")
        return SimpleNamespace(
            image=buffer.getvalue(), width=10, height=10, captured_at=1.0
        )


class Control:
    def preflight(self): return {}
    def mouse_home(self): pass
    def mouse_move(self, dx, dy): pass
    def mouse_click(self): pass


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


def test_ocr_engine_is_available_through_ios_session():
    from artemis.perception.base import Target

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
        def __init__(self): self.calls = []
        async def tap_abs(self, x, y):
            self.calls.append((x, y))
            return ActionResult(True, "tap_abs")

    sink = Sink()
    session = DeviceSession([], [sink], ScreenGeometry((449, 974), (1080, 2400)))
    target = Target("闪购", (0.296, 0.0719, 0.394, 0.0955), 0.99, "ocr")

    result = asyncio.run(tap_target(session, target))

    assert result.ok is True
    assert sink.calls == [(373, 201)]
