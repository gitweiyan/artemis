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
