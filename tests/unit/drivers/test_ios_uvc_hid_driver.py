import asyncio
from types import SimpleNamespace

from artemis.control.base import ActionResult
from artemis.devices.capabilities import CapabilitySet
from artemis.devices.geometry import ScreenGeometry
from artemis.drivers.ios.ios_uvc_hid_driver import IOSUvcHidDriver


class Source:
    capabilities = CapabilitySet()
    async def snapshot(self):
        return SimpleNamespace(image=b'png', width=449, height=974, health='ok', captured_at=1.0)


class Sink:
    capabilities = CapabilitySet(supports_relative_pointer=True, requires_pointer_precheck=True)
    def __init__(self): self.calls = []
    async def tap_abs(self, x, y):
        self.calls.append((x, y))
        return ActionResult(True, 'tap_abs')
    async def tap_rel(self, dx, dy): return ActionResult(True, 'tap_rel')
    async def swipe(self, *a, **k): return ActionResult(True, 'swipe')
    async def input_text(self, text): return ActionResult(True, 'input_text')
    async def press_key(self, key): return ActionResult(True, 'press_key')


class Session:
    def __init__(self):
        self.perception = [Source()]
        self.control = [Sink()]
        self.geometry = ScreenGeometry((449, 974), (1080, 2400))
        self.pointer = SimpleNamespace(connected=False, homed=0)
        def home(): self.pointer.homed += 1
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

    assert data.platform == 'ios'
    assert data.width == 449
    assert ok is True
    assert session.control[0].calls == [(100, 200)]
    assert session.pointer.connected is True
    assert session.pointer.homed == 1
