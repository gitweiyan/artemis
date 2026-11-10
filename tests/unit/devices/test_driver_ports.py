"""Driver → Session/Ports adapter (ADR-006 Phase 3a)."""

from __future__ import annotations

import asyncio

import pytest

from artemis.devices.driver_ports import DriverSessionAdapter
from artemis.devices.session import DeviceSessionError
from artemis.drivers.base import BaseDeviceDriver
from artemis.drivers.mock.mock_driver import MockDeviceDriver


def test_the_legacy_driver_is_marked_as_a_facade():
    assert BaseDeviceDriver.legacy_facade is True


def test_the_adapter_exposes_the_driver_session_as_ports():
    driver = MockDeviceDriver(device_id="mock-port", width=1080, height=2400)
    adapter = DriverSessionAdapter(driver)

    frame = asyncio.run(adapter.snapshot())
    assert frame.width == 1080
    assert adapter.observation() is adapter.session.perception[0]

    result = asyncio.run(adapter.tap_abs(540, 1200))
    assert result.ok is True
    assert driver.action_history[-1]["action"] == "tap"


def test_relative_control_is_refused_without_a_capability():
    driver = MockDeviceDriver(device_id="mock-port", width=1080, height=2400)
    adapter = DriverSessionAdapter(driver)

    with pytest.raises(DeviceSessionError, match="relative"):
        adapter.control(absolute=False)


def test_an_existing_driver_session_is_reused_not_rebuilt():
    from artemis.devices.session import DeviceSession

    driver = MockDeviceDriver(device_id="mock-port", width=1080, height=2400)
    existing = DeviceSession([], [], None)
    driver.session = existing  # type: ignore[attr-defined]

    adapter = DriverSessionAdapter(driver)

    assert adapter.session is existing
