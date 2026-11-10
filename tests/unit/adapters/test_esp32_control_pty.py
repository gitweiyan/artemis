"""End-to-end host path over a pseudo-terminal (no physical board)."""

from __future__ import annotations

import os
import pty
import select
import sys
import threading

import pytest

from artemis.adapters.esp32_control import Esp32BridgeControl

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="pty is POSIX-only")


HELLO = (
    "hello proto=2 fw=test123 board=esp32s3 mode=ios max_line=126 "
    "features=usb,mouse,keyboard,abs_pointer,abs_click\n"
)
USB = "usb mounted=1 suspended=0 mouse=1 keyboard=1 touch=0 mode=ios\n"


def _fake_firmware(master: int, seen: list[str], stop: threading.Event) -> None:
    buffer = b""
    while not stop.is_set():
        readable, _, _ = select.select([master], [], [], 0.1)
        if not readable:
            continue
        chunk = os.read(master, 4096)
        if not chunk:
            return
        buffer += chunk
        while b"\n" in buffer:
            raw, buffer = buffer.split(b"\n", 1)
            command = raw.decode("ascii", "replace").strip()
            if not command:
                continue
            seen.append(command)
            if command == "hello":
                os.write(master, (HELLO + "OK\n").encode())
            elif command == "usb":
                os.write(master, (USB + "OK\n").encode())
            else:
                os.write(master, b"OK\n")


def test_control_handshake_and_gesture_roundtrip_over_pty():
    master, slave = pty.openpty()
    seen: list[str] = []
    stop = threading.Event()
    thread = threading.Thread(target=_fake_firmware, args=(master, seen, stop), daemon=True)
    thread.start()
    control = Esp32BridgeControl(os.ttyname(slave), ack_timeout_s=1.0)
    try:
        state = control.preflight()
        assert state["mounted"] == 1
        assert state["protocol"] == 2
        assert control.firmware_info is not None
        assert control.firmware_info.build == "test123"

        control.mouse_move(200, -130)
        assert "move 127 -127" in seen
        assert "move 73 -3" in seen
    finally:
        control.close()
        stop.set()
        thread.join(timeout=1.0)
        os.close(master)
        os.close(slave)
