import asyncio
from types import SimpleNamespace

from artemis.control.base import ActionResult
from artemis.devices.capabilities import CapabilitySet
from artemis.devices.geometry import ScreenGeometry
from artemis.drivers.ios.ios_uvc_hid_driver import IOSUvcHidDriver


class Source:
    capabilities = CapabilitySet()

    async def snapshot(self):
        return SimpleNamespace(image=b"png", width=449, height=974, health="ok", captured_at=1.0)


class Sink:
    capabilities = CapabilitySet(supports_relative_pointer=True, requires_pointer_precheck=True)

    def __init__(self):
        self.calls = []

    async def tap_abs(self, x, y):
        self.calls.append((x, y))
        return ActionResult(True, "tap_abs")

    async def tap_rel(self, dx, dy):
        return ActionResult(True, "tap_rel")

    async def swipe(self, *a, **k):
        return ActionResult(True, "swipe")

    async def input_text(self, text):
        return ActionResult(True, "input_text")

    async def press_key(self, key):
        return ActionResult(True, "press_key")


class Session:
    def __init__(self):
        self.perception = [Source()]
        self.control = [Sink()]
        self.geometry = ScreenGeometry((449, 974), (1080, 2400))
        self.pointer = SimpleNamespace(connected=False, homed=0)

        def home():
            self.pointer.homed += 1

        self.pointer.home = home


def test_capabilities_declare_relative_hid_only():
    caps = IOSUvcHidDriver.capabilities
    assert caps.supports_relative_pointer is True
    assert caps.supports_ui_tree is False
    assert caps.supports_absolute_tap is False


def test_screenshot_and_tap_go_through_session():
    session = Session()
    driver = IOSUvcHidDriver(session)
    asyncio.run(driver.connect())
    data = asyncio.run(driver.get_screen_data())
    ok = asyncio.run(driver.tap(100, 200))

    assert data.platform == "ios"
    assert data.width == 1080, "device pixels, not the cropped frame"
    assert ok is True
    assert session.control[0].calls == [(100, 200)]
    assert session.pointer.connected is True
    assert session.pointer.homed == 1


def _ring_png():
    import cv2
    import numpy as np

    image = np.full((200, 200, 3), 180, dtype=np.uint8)
    cv2.circle(image, (100, 100), 20, (0, 0, 255), 4)
    return cv2.imencode(".png", image)[1].tobytes()


def test_validate_pointer_inside_target():
    from artemis.perception.base import Target

    session = Session()
    session.perception[0].snapshot = lambda: __import__("asyncio").sleep(
        0,
        result=SimpleNamespace(
            image=_ring_png(), width=200, height=200, health="ok", captured_at=1.0
        ),
    )
    driver = IOSUvcHidDriver(session)
    result = asyncio.run(
        driver.validate_pointer(Target("target", (0.4, 0.4, 0.6, 0.6), 0.9, "ocr"))
    )
    assert result.ok is True
    assert result.incident is None


def test_validate_pointer_off_target():
    from artemis.perception.base import Target

    session = Session()
    session.perception[0].snapshot = lambda: __import__("asyncio").sleep(
        0,
        result=SimpleNamespace(
            image=_ring_png(), width=200, height=200, health="ok", captured_at=1.0
        ),
    )
    driver = IOSUvcHidDriver(session)
    result = asyncio.run(
        driver.validate_pointer(Target("target", (0.0, 0.0, 0.2, 0.2), 0.9, "ocr"))
    )
    assert result.ok is False
    assert result.incident == "pointer_off_target"


def test_screen_data_reports_the_device_screen_not_the_capture_frame():
    """The observation layer writes this size onto ctx.device and converts every
    normalized coordinate with it; the cropped frame is 2.4x smaller on this rig."""
    session = Session()
    driver = IOSUvcHidDriver(session)

    data = asyncio.run(driver.get_screen_data())

    assert (data.width, data.height) == (1080, 2400)
    assert len(data.screenshot_bytes) > 0, "the image itself is still the frame"


def test_the_driver_instance_keeps_its_own_no_app_launch_declaration():
    session = Session()
    driver = IOSUvcHidDriver(session)

    assert driver.capabilities.supports_app_launch is False
    assert driver.capabilities.supports_ui_tree is False
    assert driver.capabilities.requires_pointer_precheck is True


# --- frame health: no signal must never look like a successful observation --------------


def _session_with_frame(**frame_fields):
    from types import SimpleNamespace

    session = Session()
    healthy = SimpleNamespace(
        image=frame_fields.get("image", b"png"),
        width=frame_fields.get("width", 449),
        height=frame_fields.get("height", 974),
        health=frame_fields.get("health", "ok"),
        captured_at=1.0,
    )

    async def snapshot():
        return healthy

    session.perception[0].snapshot = snapshot
    return session


def test_a_black_frame_is_an_incident_not_a_screenshot():
    import pytest

    from artemis.perception.incidents import UVC_NO_SIGNAL, UnhealthyFrame

    driver = IOSUvcHidDriver(_session_with_frame(health="black"))

    with pytest.raises(UnhealthyFrame) as excinfo:
        asyncio.run(driver.get_screen_data())

    assert excinfo.value.incident == UVC_NO_SIGNAL
    assert "black" in str(excinfo.value)


def test_a_capture_that_never_produced_a_frame_is_a_timeout_incident():
    import pytest

    from artemis.perception.incidents import CAPTURE_TIMEOUT, UnhealthyFrame
    from artemis.reflex.contract import CaptureError

    session = Session()

    async def explode():
        raise CaptureError("uvc capture failed: Command ... timed out after 12.0 seconds")

    session.perception[0].snapshot = explode
    driver = IOSUvcHidDriver(session)

    with pytest.raises(UnhealthyFrame) as excinfo:
        asyncio.run(driver.get_screen_data())

    assert excinfo.value.incident == CAPTURE_TIMEOUT


def test_a_healthy_frame_still_returns_screen_data():
    driver = IOSUvcHidDriver(_session_with_frame())

    data = asyncio.run(driver.get_screen_data())

    assert (data.width, data.height) == (1080, 2400)
    assert data.screenshot_bytes == b"png"


def test_a_capture_failure_during_a_tap_becomes_an_incident_not_an_exception():
    """The pointer loop needs frames; if none arrive, the tap fails with a code."""
    from artemis.perception.incidents import CAPTURE_TIMEOUT
    from artemis.reflex.contract import CaptureError

    class FailingCapture:
        def __init__(self):
            from artemis.devices.geometry import ScreenGeometry

            from artemis.control.hid import HidControlSink
            from artemis.reflex.mouse import PointerSession, SessionTapController

            control = SimpleNamespace(
                preflight=lambda: {},
                mouse_home=lambda: None,
                mouse_move=lambda dx, dy: None,
                mouse_click=lambda: None,
            )
            pointer = PointerSession(control, frame_size=(449, 974), gain=(1.8, 1.8))
            pointer.connected = True
            controller = SessionTapController(pointer, capture=self, detector=_Never())
            self.session = SimpleNamespace(
                perception=[Source()],
                control=[HidControlSink(pointer, controller)],
                geometry=ScreenGeometry((449, 974), (1080, 2400)),
                pointer=pointer,
            )

        def grab_latest(self):
            raise CaptureError("uvc capture failed: Command ... timed out after 12.0 seconds")

    class _Never:
        def detect(self, image, near=None):
            return None

    driver = IOSUvcHidDriver(FailingCapture().session)

    result = asyncio.run(driver.tap_with_result(500, 500))

    assert result.ok is False
    assert result.incident == CAPTURE_TIMEOUT
    assert "timed out" in result.detail
