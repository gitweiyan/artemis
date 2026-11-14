"""R4 adapter contracts: the same Ports, exercised across real adapter classes.

These are intentionally narrower than the algorithm tests: they pin the
*interface* every Observation/Actuation adapter must satisfy so a new platform
cannot quietly ship a source/sink with a different shape.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from artemis.control.app_control import AppControlSink
from artemis.control.base import ActionResult, ControlSink
from artemis.control.composite import CompositeControlSink
from artemis.control.hid import HidControlSink
from artemis.core.capabilities import CapabilitySet
from artemis.perception.app_automation import AppAutomationSource
from artemis.perception.base import Frame, PerceptionSource
from artemis.perception.composite import CompositePerception
from artemis.perception.uvc import UvcSource
from artemis.devices.geometry import ScreenGeometry
from artemis.devices.session import DeviceSession


class _Capture:
    def grab(self):
        return SimpleNamespace(
            image=b"frame",
            captured_at=10.0,
            width=449,
            height=974,
        )


class _Source:
    def __init__(self, health="ok", name="fake"):
        self.capabilities = CapabilitySet(supports_ui_tree=True)
        self.health = health
        self.name = name
        self.closed = False

    async def snapshot(self):
        return Frame(
            image=b"frame",
            captured_at=1.0,
            width=449,
            height=974,
            source=self.name,
            health=self.health,
        )

    async def ui_tree(self):
        return []

    async def ocr(self, frame):
        return None

    async def locate(self, frame, target):
        return None

    def close(self):
        self.closed = True


def _sources():
    driver = SimpleNamespace(
        get_screen_data=lambda: _async_data(
            SimpleNamespace(
                screenshot_bytes=b"frame",
                timestamp=1.0,
                width=1080,
                height=2400,
                ui_elements=[],
            )
        )
    )
    return [
        UvcSource(_Capture(), name="uvc-contract"),
        AppAutomationSource(driver, name="app-contract"),
        CompositePerception([_Source()]),
    ]


async def _async_data(value):
    return value


@pytest.mark.parametrize("source", _sources(), ids=["uvc", "app", "composite"])
def test_every_perception_adapter_satisfies_the_source_contract(source):
    assert isinstance(source, PerceptionSource)
    assert isinstance(source.capabilities, CapabilitySet)

    frame = asyncio.run(source.snapshot())
    assert isinstance(frame, Frame)
    assert frame.width > 0 and frame.height > 0
    assert frame.source
    assert frame.health in {"ok", "black", "decode_error", "timeout", "unknown"}


def test_composite_perception_reports_the_source_that_was_used():
    composite = CompositePerception([_Source(health="black", name="dark"), _Source(name="second")])

    frame = asyncio.run(composite.snapshot())

    assert frame.source == "second"


def test_app_automation_source_exposes_a_ui_tree_after_observation():
    async def data():
        return SimpleNamespace(
            screenshot_bytes=b"frame",
            timestamp=1.0,
            width=1080,
            height=2400,
            ui_elements=[{"text": "Settings", "bounds": [0, 0, 100, 100]}],
        )

    source = AppAutomationSource(SimpleNamespace(get_screen_data=data))

    tree = asyncio.run(source.ui_tree())

    assert tree and tree[0].text == "Settings"
    assert tree[0].bounds == (0, 0, 100, 100)


class _Sink:
    def __init__(self, *, absolute=False, relative=False, text=False):
        self.capabilities = CapabilitySet(
            supports_absolute_tap=absolute,
            supports_relative_pointer=relative,
            supports_text_input=text,
        )
        self.calls = []

    async def tap_abs(self, x, y):
        self.calls.append(("tap_abs", x, y))
        return ActionResult(True, "tap_abs")

    async def tap_rel(self, dx, dy):
        self.calls.append(("tap_rel", dx, dy))
        return ActionResult(True, "tap_rel")

    async def swipe(self, *args, **kwargs):
        self.calls.append(("swipe", args, kwargs))
        return ActionResult(True, "swipe")

    async def input_text(self, text):
        self.calls.append(("input_text", text))
        return ActionResult(True, "input_text")

    async def press_key(self, key):
        self.calls.append(("press_key", key))
        return ActionResult(True, "press_key")


def test_composite_control_sink_is_a_control_sink_and_chooses_by_capability():
    absolute = _Sink(absolute=True)
    relative = _Sink(relative=True)
    composite = CompositeControlSink([absolute, relative])

    assert isinstance(composite, ControlSink)
    assert asyncio.run(composite.tap_abs(10, 20)).ok
    assert absolute.calls == [("tap_abs", 10, 20)]
    assert asyncio.run(composite.tap_rel(3, 4)).ok
    assert relative.calls == [("tap_rel", 3, 4)]


def test_composite_control_sink_refuses_an_action_no_surface_can_express():
    composite = CompositeControlSink([_Sink(absolute=True)])

    result = asyncio.run(composite.input_text("hello"))

    assert result.ok is False
    assert result.incident == "unsupported"


def test_hid_control_sink_satisfies_the_control_contract_shape():
    session = SimpleNamespace(control=object())
    sink = HidControlSink(session, object())

    assert isinstance(sink, ControlSink)
    assert isinstance(sink.capabilities, CapabilitySet)
    assert sink.capabilities.supports_relative_pointer


def test_app_control_sink_preserves_the_action_result_contract():
    class Driver:
        async def tap(self, x, y):
            return True

    sink = AppControlSink(Driver())

    assert isinstance(sink, ControlSink)
    assert asyncio.run(sink.tap_abs(10, 20)).ok is True
    refused = asyncio.run(sink.tap_rel(1, 2))
    assert refused.ok is False
    assert refused.incident == "unsupported"


def test_device_session_contract_is_platform_neutral():
    """Android, iOS and future Profiles must fit the same source/sink/session shape."""

    source = _Source(name="platform-neutral")
    sink = _Sink(absolute=True)
    session = DeviceSession(
        [source],
        [sink],
        ScreenGeometry((449, 974), (1080, 2400)),
    )

    assert isinstance(source, PerceptionSource)
    assert isinstance(sink, ControlSink)
    assert session.control_for(absolute=True) is sink
    frame = asyncio.run(session.snapshot())
    assert frame.source == "platform-neutral"
    result = asyncio.run(session.control_for(absolute=True).tap_abs(10, 20))
    assert isinstance(result, ActionResult)
