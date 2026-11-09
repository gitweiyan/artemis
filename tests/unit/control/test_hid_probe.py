"""HID capability probes (ADR-001 PR-2c)."""

from __future__ import annotations

from types import SimpleNamespace

from artemis.control.hid import HidControlSink
from artemis.core.capabilities import CapabilityId


class _Control:
    def __init__(self, state=None, error=None):
        self.state = state or {
            "mounted": 1,
            "suspended": 0,
            "mouse": 1,
            "keyboard": 1,
            "touch": 0,
        }
        self.error = error

    def preflight(self):
        if self.error is not None:
            raise self.error
        return self.state


def _sink(control):
    return HidControlSink(SimpleNamespace(control=control), object())


def test_a_ready_board_declares_and_probes_its_interfaces():
    sink = _sink(_Control())
    probes = sink.probe_capabilities()

    assert probes[CapabilityId.SERIAL_COMMAND].available is True
    assert probes[CapabilityId.USB_HID].available is True
    assert probes[CapabilityId.POINTER_RELATIVE].available is True
    assert probes[CapabilityId.KEYBOARD_KEY].available is True
    assert probes[CapabilityId.TEXT_INPUT].available is True


def test_a_failed_preflight_only_marks_the_command_channel_unavailable():
    sink = _sink(_Control(error=RuntimeError("not mounted")))
    probes = sink.probe_capabilities()

    assert probes[CapabilityId.SERIAL_COMMAND].available is False
    assert "preflight_failed" in (probes[CapabilityId.SERIAL_COMMAND].reason or "")
    assert CapabilityId.USB_HID not in probes


def test_the_hid_manifest_matches_the_control_capabilities():
    manifest = _sink(_Control()).capability_manifest
    assert CapabilityId.POINTER_RELATIVE in manifest.declared_ids
    assert CapabilityId.USB_HID in manifest.declared_ids
    assert CapabilityId.TEXT_INPUT in manifest.declared_ids
    assert CapabilityId.KEYBOARD_KEY in manifest.declared_ids
