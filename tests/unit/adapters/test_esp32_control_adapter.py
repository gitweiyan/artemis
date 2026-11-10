"""The bridge-backed Control adapter keeps the legacy HID control surface."""

from __future__ import annotations

import pytest

from artemis.adapters.esp32_control import Esp32BridgeControl
from artemis.adapters.firmware import FirmwareInfo


class FakeBridge:
    def __init__(self, info: FirmwareInfo, state: FirmwareInfo):
        self.info = info
        self.state = state
        self.commands: list[str] = []
        self.closed = False

    def handshake(self):
        return self.info

    def preflight(self):
        return self.state

    def exchange(self, command: str, *, timeout=None):
        self.commands.append(command)
        return "OK\n"

    def resync(self):
        pass

    def close(self):
        self.closed = True


def _adapter(protocol=2):
    info = FirmwareInfo(
        protocol=protocol,
        build="abc123",
        board="esp32s3",
        mode="ios",
        mouse=True,
        keyboard=True,
    )
    state = FirmwareInfo(
        protocol=protocol,
        build="abc123",
        board="esp32s3",
        mode="ios",
        mounted=True,
        mouse=True,
        keyboard=True,
    )
    bridge = FakeBridge(info, state)
    return Esp32BridgeControl("FAKE", bridge=bridge), bridge


def test_preflight_returns_the_legacy_state_and_keeps_firmware_facts():
    control, bridge = _adapter()

    state = control.preflight()

    assert state["mounted"] == 1
    assert state["mode"] == "ios"
    assert state["protocol"] == 2
    assert state["firmware"] == "abc123"
    assert control.firmware_info is not None
    assert control.firmware_info.mounted is True
    assert bridge.commands == []


def test_gestures_go_through_the_bridge_and_release_on_close():
    control, bridge = _adapter()

    control.mouse_move(200, -130)
    control.key_down(4)
    control.close()

    assert bridge.commands == ["move 127 -127", "move 73 -3", "kdown 4", "kup 4"]
    assert bridge.closed is True


def test_a_newer_firmware_is_refused_before_input():
    control, bridge = _adapter(protocol=3)

    with pytest.raises(RuntimeError, match="newer"):
        control.preflight()

    assert bridge.commands == []
