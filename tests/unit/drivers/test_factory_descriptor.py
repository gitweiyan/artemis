"""The factory attaches a read-only DeviceDescriptor without changing selection."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from artemis.core.identity import EndpointKind, PlatformFamily
from artemis.drivers.factory import create_driver
from artemis.drivers.ios.ios_uvc_hid_driver import IOSUvcHidDriver
from artemis.drivers.mock.mock_driver import MockDeviceDriver


def _ctx(platform: str, device_id: str, width: int = 1080, height: int = 2400):
    return SimpleNamespace(
        device=SimpleNamespace(
            mobile_platform=platform,
            device_id=device_id,
            device_width=width,
            device_height=height,
        ),
        adb_client=None,
        ui_adb_client=None,
    )


@pytest.fixture(autouse=True)
def _stable_host(monkeypatch):
    monkeypatch.setenv("ARTEMIS_HOST_ID", "test-host")
    monkeypatch.delenv("ARTEMIS_DEVICE_ALIAS", raising=False)


def test_ios_factory_attaches_a_two_endpoint_descriptor(monkeypatch):
    monkeypatch.setenv("ARTEMIS_ESP32_PORT", "/dev/fake-esp32")
    ctx = _ctx("ios", "ios-uvc")

    with (
        patch("artemis.adapters.uvc.UvcCapture"),
        patch("artemis.adapters.esp32_control.Esp32BridgeControl"),
        patch("artemis.drivers.factory.default_ocr_engine"),
    ):
        driver = create_driver(ctx)

    assert isinstance(driver, IOSUvcHidDriver)
    descriptor = ctx.device_descriptor
    assert descriptor.device_id == "ios-uvc"
    assert descriptor.platform_family is PlatformFamily.IOS
    assert [endpoint.kind for endpoint in descriptor.endpoints] == [
        EndpointKind.UVC,
        EndpointKind.SERIAL,
    ]


def test_mock_factory_attaches_a_mock_descriptor():
    ctx = _ctx("mock", "mock-device")

    driver = create_driver(ctx)

    assert isinstance(driver, MockDeviceDriver)
    assert ctx.device_descriptor.platform_family is PlatformFamily.MOCK
    assert ctx.device_descriptor.endpoints[0].endpoint_id == "mock:mock-device"


def test_android_factory_attaches_an_adb_descriptor():
    ctx = _ctx("android", "emulator-5554")
    ctx.adb_client = MagicMock()

    driver = create_driver(ctx)

    descriptor = ctx.device_descriptor
    assert descriptor.device_id == "emulator-5554"
    assert descriptor.platform_family is PlatformFamily.ANDROID
    assert [endpoint.kind for endpoint in descriptor.endpoints] == [EndpointKind.ADB]
    assert driver.device_id == "emulator-5554"


def test_ios_factory_leases_the_uvc_and_serial_resources_until_session_close(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTEMIS_ESP32_PORT", "/dev/fake-esp32")
    monkeypatch.setenv("ARTEMIS_RESOURCE_LEASE", "1")
    monkeypatch.setenv("ARTEMIS_RESOURCE_LEASE_DIR", str(tmp_path))
    ctx = _ctx("ios", "ios-uvc")

    with (
        patch("artemis.adapters.uvc.UvcCapture"),
        patch("artemis.adapters.esp32_control.Esp32BridgeControl"),
        patch("artemis.drivers.factory.default_ocr_engine"),
    ):
        driver = create_driver(ctx)

    assert driver.session.leases is not None
    leases = list(tmp_path.glob("resource-*.lock"))
    assert len(leases) == 2, "UVC capture and ESP32 serial each need a lease"

    asyncio.run(ctx.device_session.close())
    assert not list(tmp_path.glob("resource-*.lock")), "closing the session releases both"


def test_ios_factory_attaches_an_evidence_registry_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTEMIS_ESP32_PORT", "/dev/fake-esp32")
    monkeypatch.setenv("ARTEMIS_CAPABILITY_EVIDENCE", "1")
    monkeypatch.setenv("ARTEMIS_CAPABILITY_DB", str(tmp_path / "evidence.db"))
    monkeypatch.setenv("ARTEMIS_CAPABILITY_ARTIFACTS", str(tmp_path / "artifacts"))
    ctx = _ctx("ios", "ios-uvc")

    with (
        patch("artemis.adapters.uvc.UvcCapture"),
        patch("artemis.adapters.esp32_control.Esp32BridgeControl"),
        patch("artemis.drivers.factory.default_ocr_engine"),
    ):
        driver = create_driver(ctx)

    assert driver.session.capability_registry is not None
    assert driver.session.descriptor is not None
    assert driver.session.descriptor.fingerprint["adapter_id"] == "esp32_hid"
    asyncio.run(driver.session.close())


def test_android_factory_attaches_a_runtime_owned_session():
    from artemis.devices.session import DeviceSession

    ctx = _ctx("android", "emulator-5554")
    ctx.adb_client = MagicMock()

    driver = create_driver(ctx)

    assert isinstance(ctx.device_session, DeviceSession)
    assert driver.session is ctx.device_session


def test_ios_factory_passes_the_profile_port_hint_to_the_resolver(monkeypatch, tmp_path):
    from artemis.core.identity import IdentitySource
    from artemis.drivers.ios.uvc_identity import UVCIdentity

    monkeypatch.setenv("ARTEMIS_ESP32_PORT", "/dev/fake-esp32")
    monkeypatch.setenv("ARTEMIS_UVC_PORT_HINT", "1-2.4")
    monkeypatch.setenv("ARTEMIS_RESOURCE_LEASE", "0")
    ctx = _ctx("ios", "ios-uvc")
    identity = UVCIdentity("USB-Video", IdentitySource.CONFIG_HINT)

    with (
        patch("artemis.adapters.uvc.UvcCapture"),
        patch("artemis.adapters.esp32_control.Esp32BridgeControl"),
        patch("artemis.drivers.factory.default_ocr_engine"),
        patch(
            "artemis.drivers.ios.uvc_identity.resolve_uvc_identity",
            return_value=identity,
        ) as resolver,
    ):
        create_driver(ctx)

    assert resolver.call_args.kwargs["port_hint"] == "1-2.4"
