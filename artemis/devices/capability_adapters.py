"""Built-in adapter metadata + the Mock profile builder (Phase 2a).

Adapters are declared here but their I/O stays in the existing modules until
Phase 4. Declarations give profile selection a capability manifest without
opening a device.
"""

from __future__ import annotations

from typing import Any

from artemis.core.capabilities import CapabilityDeclaration, CapabilityId, CapabilityManifest
from artemis.devices.composition import CompositionRequest

__all__ = [
    "android_adapters",
    "build_mock_profile",
    "ios_adapters",
    "mock_adapters",
]


def _manifest(adapter_id: str, *capabilities: str) -> CapabilityManifest:
    return CapabilityManifest(
        adapter_id=adapter_id,
        adapter_version="1",
        declarations=tuple(CapabilityDeclaration(capability) for capability in capabilities),
    )


def android_adapters():
    from artemis.devices.registry import AdapterFactory

    return (
        AdapterFactory(
            "android.app",
            "1",
            _manifest(
                "android.app",
                CapabilityId.UI_TREE_READ,
                CapabilityId.TOUCH_ABSOLUTE,
                CapabilityId.TEXT_INPUT,
                CapabilityId.APP_LAUNCH,
                CapabilityId.SHELL_EXEC,
            ),
        ),
    )


def ios_adapters():
    from artemis.devices.registry import AdapterFactory

    return (
        AdapterFactory(
            "uvc.capture",
            "1",
            _manifest("uvc.capture", CapabilityId.FRAME_CAPTURE, CapabilityId.OCR_READ),
        ),
        AdapterFactory(
            "esp32_hid",
            "1",
            _manifest(
                "esp32_hid",
                CapabilityId.POINTER_RELATIVE,
                CapabilityId.POINTER_ABSOLUTE,
                CapabilityId.USB_HID,
                CapabilityId.KEYBOARD_KEY,
                CapabilityId.TEXT_INPUT,
            ),
        ),
        AdapterFactory(
            "serial.command",
            "1",
            _manifest("serial.command", CapabilityId.SERIAL_COMMAND),
        ),
    )


def mock_adapters():
    from artemis.devices.registry import AdapterFactory

    return (
        AdapterFactory(
            "mock.screen",
            "1",
            _manifest("mock.screen", CapabilityId.FRAME_CAPTURE),
        ),
    )


def build_mock_profile(request: CompositionRequest, _registry: Any):
    from artemis.devices.app_session import build_app_session
    from artemis.devices.registry import ProfileBuild
    from artemis.drivers.mock.mock_driver import MockDeviceDriver

    width, height = request.screen_size or (1080, 2400)
    driver = MockDeviceDriver(
        device_id=request.device_id or "mock-device", width=width, height=height
    )
    session = build_app_session(driver, screen_size=(width, height))
    return ProfileBuild(
        profile_id="mock.screen",
        session=session,
        driver=driver,
        adapters={"mock.screen": driver},
    )
