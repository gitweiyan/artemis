"""P0: one command in flight, wait for the firmware's ``OK`` before the next.

The 2026-10-19 failure was a UART overrun: bursts of moves left the firmware
parser reading single characters (``unknown command: ove``). The transport now
serialises commands on this ack, so a slow ``home``/``move`` cannot be overrun by
the next line.
"""

from __future__ import annotations

import socket
import threading

import pytest

from artemis.reflex.control import Esp32HidControl


def _socketpair():
    return socket.socketpair()


def _responder(sock: socket.socket, reply: bytes, seen: list[bytes]) -> threading.Thread:
    def run() -> None:
        data = sock.recv(4096)
        seen.append(data)
        sock.sendall(reply)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def test_send_with_response_waits_for_the_ok_ack():
    host, firmware = _socketpair()
    control = Esp32HidControl("FAKE", ack_timeout_s=1.0)
    control._fd = host.fileno()
    seen: list[bytes] = []
    thread = _responder(firmware, b"[cmd] move 1 2\r\nOK\r\n", seen)
    try:
        response = control._send_with_response("move 1 2")
    finally:
        thread.join(timeout=1.0)
        control._fd = None
        host.close()
        firmware.close()

    assert "OK" in response
    assert seen == [b"move 1 2\n"]


def test_send_times_out_when_the_firmware_never_acknowledges():
    host, firmware = _socketpair()
    control = Esp32HidControl("FAKE", ack_timeout_s=0.1)
    control._fd = host.fileno()
    try:
        with pytest.raises(RuntimeError):
            control._send_with_response("move 1 2")
    finally:
        control._fd = None
        host.close()
        firmware.close()


def test_mouse_move_goes_through_the_ack_channel_without_host_side_splitting():
    host, firmware = _socketpair()
    control = Esp32HidControl("FAKE", ack_timeout_s=1.0)
    control._fd = host.fileno()
    seen: list[bytes] = []
    thread = _responder(firmware, b"[cmd] move 300 -300\r\nOK\r\n", seen)
    try:
        control.mouse_move(300, -300)
    finally:
        thread.join(timeout=1.0)
        control._fd = None
        host.close()
        firmware.close()

    assert seen == [b"move 300 -300\n"], (
        "the firmware owns int8 splitting; the host sends the intent once"
    )


def test_an_err_response_is_a_failure_even_though_it_ends_with_ok():
    host, firmware = _socketpair()
    control = Esp32HidControl("FAKE", ack_timeout_s=1.0)
    control._fd = host.fileno()
    seen: list[bytes] = []
    thread = _responder(firmware, b"ERR mouse not ready\r\nOK\r\n", seen)
    try:
        with pytest.raises(RuntimeError, match="mouse not ready"):
            control._send_with_response("move 1 2")
    finally:
        thread.join(timeout=1.0)
        control._fd = None
        host.close()
        firmware.close()


def test_an_overlong_command_is_rejected_before_it_reaches_the_transport():
    control = Esp32HidControl("FAKE")
    seen: list[str] = []
    control._writer = lambda line: seen.append(line)

    with pytest.raises(ValueError, match="too long"):
        control._send_with_response("type " + "x" * 200)

    assert seen == [], "the UART must never see an overlong line"
