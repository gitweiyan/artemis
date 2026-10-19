import asyncio

import pytest

from artemis.devices.capabilities import CapabilitySet
from artemis.devices.geometry import ScreenGeometry
from artemis.devices.session import DeviceSession, DeviceSessionError
from artemis.perception.base import Frame


class Source:
    def __init__(self, frame=None, error=None):
        self.capabilities = CapabilitySet(supports_ocr=True)
        self.frame = frame
        self.error = error

    async def snapshot(self):
        if self.error:
            raise self.error
        return self.frame


class Sink:
    def __init__(self, absolute=False, relative=False):
        self.capabilities = CapabilitySet(
            supports_absolute_tap=absolute,
            supports_relative_pointer=relative,
            requires_pointer_precheck=relative,
        )


def geometry():
    return ScreenGeometry((449, 974), (1080, 2400))


def test_snapshot_uses_first_healthy_source():
    bad = Frame(b"bad", 1.0, 10, 10, "bad", health="black")
    good = Frame(b"ok", 2.0, 10, 10, "good")
    session = DeviceSession([Source(bad), Source(good)], [], geometry())

    assert asyncio.run(session.snapshot()).image == b"ok"


def test_all_sources_fail_with_typed_error():
    session = DeviceSession([Source(error=RuntimeError("x"))], [], geometry())

    with pytest.raises(DeviceSessionError):
        asyncio.run(session.snapshot())


def test_control_selection_is_capability_based():
    abs_sink, rel_sink = Sink(absolute=True), Sink(relative=True)
    session = DeviceSession([], [rel_sink, abs_sink], geometry())

    assert session.control_for(absolute=True) is abs_sink
    assert session.control_for(absolute=False) is rel_sink


def test_missing_control_raises():
    session = DeviceSession([], [], geometry())

    with pytest.raises(DeviceSessionError):
        session.control_for(absolute=False)


def test_capabilities_union_across_sources_and_sinks():
    session = DeviceSession(
        [Source()],
        [Sink(relative=True)],
        geometry(),
    )
    assert session.capabilities.supports_ocr is True
    assert session.capabilities.supports_relative_pointer is True
    assert session.capabilities.supports_absolute_tap is False
