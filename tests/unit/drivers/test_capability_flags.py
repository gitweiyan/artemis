from artemis.devices.capabilities import CapabilitySet
from artemis.drivers.android.adb_driver import AndroidAdbDriver
from artemis.drivers.base import BaseDeviceDriver


def test_base_driver_exposes_capability_defaults():
    assert BaseDeviceDriver.capabilities == CapabilitySet()


def test_android_declares_ui_tree_and_absolute_tap():
    caps = AndroidAdbDriver.capabilities
    assert caps.supports_ui_tree is True
    assert caps.supports_absolute_tap is True
    assert caps.requires_pointer_precheck is False
