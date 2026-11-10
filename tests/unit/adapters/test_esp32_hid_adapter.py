"""ESP32 HID adapter command mapping over the firmware bridge."""

from __future__ import annotations

import pytest

from artemis.adapters.esp32_hid import Esp32HidAdapter
from artemis.adapters.firmware import FirmwareBridge


class FakeTransport:
    def __init__(self, usb=b"usb mounted=1 suspended=0 mouse=1 keyboard=1 touch=0 mode=ios\nOK\n"):
        self.usb = usb
        self.writes: list[str] = []
        self._pending: list[bytes] = []

    def write(self, data: bytes) -> None:
        if data == b"\n":
            return
        line = data.decode("ascii").rstrip("\n")
        self.writes.append(line)
        self._pending.append(self.usb if line == "usb" else b"OK\n")

    def read(self, timeout_s: float) -> bytes:
        return self._pending.pop(0) if self._pending else b""

    def close(self) -> None:
        pass


def _adapter():
    transport = FakeTransport()
    bridge = FirmwareBridge(transport, ack_timeout_s=0.2)
    return Esp32HidAdapter(bridge), transport


def test_move_is_segmented_to_the_int8_firmware_limit():
    adapter, transport = _adapter()

    adapter.move(200, -130)

    assert transport.writes == ["move 127 -127", "move 73 -3"]


def test_absolute_move_clamps_to_the_firmware_normalized_grid():
    adapter, transport = _adapter()

    adapter.move_abs(-5, 1200)

    assert transport.writes == ["amove 0 1000"]


def test_keys_and_mouse_buttons_are_tracked_for_release():
    adapter, transport = _adapter()

    adapter.key_down(4)
    adapter.button_down()
    adapter.release_all()

    assert transport.writes == ["kdown 4", "mdown", "kup 4", "mup"]
    assert adapter._pressed_keys == set()
    assert adapter._mouse_down is False


def test_text_is_ascii_only_and_fragmented_below_the_line_limit():
    adapter, transport = _adapter()

    adapter.type_text("a" * 200)

    assert all(len(line.encode("ascii")) + 1 <= 126 for line in transport.writes)
    assert "".join(line.removeprefix("type ") for line in transport.writes) == "a" * 200

    with pytest.raises(ValueError, match="ASCII"):
        adapter.type_text("设置")


def test_preflight_returns_the_bridge_info():
    adapter, transport = _adapter()

    info = adapter.preflight()

    assert info.mode == "ios"
    assert info.mouse is True
    assert transport.writes == ["usb"]
