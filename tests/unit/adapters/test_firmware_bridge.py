"""Host firmware bridge v1 semantics (ADR-009 Phase 4.2a)."""

from __future__ import annotations

import pytest

from artemis.adapters.firmware import (
    FirmwareAckTimeout,
    FirmwareBridge,
    FirmwareCommandError,
    FirmwareLineTooLong,
    MAX_LINE_BYTES,
    parse_hello_info,
    parse_usb_info,
)


class FakeTransport:
    def __init__(self, replies=None):
        self.replies = dict(replies or {})
        self.writes: list[bytes] = []
        self._pending: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.writes.append(data)
        if data == b"\n":
            return  # resync has no command response
        command = data.decode("ascii").rstrip("\n")
        reply = self.replies.get(command, b"OK\n")
        self._pending.append(reply)

    def read(self, timeout_s: float) -> bytes:
        if not self._pending:
            return b""
        return self._pending.pop(0)

    def close(self) -> None:
        self.closed = True


def _bridge(**kwargs):
    transport = FakeTransport(kwargs.pop("replies", None))
    return FirmwareBridge(transport, ack_timeout_s=0.2), transport


def test_exchange_writes_one_line_and_returns_the_ack():
    bridge, transport = _bridge()

    reply = bridge.exchange("move 10 20")

    assert reply.strip() == "OK"
    assert transport.writes == [b"move 10 20\n"]


def test_err_is_a_command_error():
    bridge, _ = _bridge(replies={"move 1 1": b"ERR bad args\nOK\n"})

    with pytest.raises(FirmwareCommandError, match="ERR bad args"):
        bridge.exchange("move 1 1")


def test_line_too_long_resyncs_and_retries_once():
    transport = FakeTransport()
    transport.replies["move 1 1"] = b"ERR line too long\nOK\n"
    bridge = FirmwareBridge(transport, ack_timeout_s=0.2)

    # First exchange gets the error; the retry needs an OK.
    transport.replies["move 1 1"] = b"ERR line too long\nOK\n"
    with pytest.raises(FirmwareCommandError):
        bridge.exchange("move 1 1")

    transport.replies["move 1 1"] = b"OK\n"
    assert bridge.exchange("move 1 1").strip() == "OK"
    assert transport.writes[:3] == [b"move 1 1\n", b"\n", b"move 1 1\n"]


def test_a_command_over_the_line_limit_never_reaches_the_transport():
    bridge, transport = _bridge()

    with pytest.raises(FirmwareLineTooLong):
        bridge.exchange("x" * MAX_LINE_BYTES)

    assert transport.writes == []


def test_timeout_is_reported_as_an_ack_timeout():
    bridge, _ = _bridge()

    class Silent(FakeTransport):
        def read(self, timeout_s: float) -> bytes:
            return b""

    bridge.transport = Silent()
    with pytest.raises(FirmwareAckTimeout):
        bridge.exchange("usb", timeout=0.01)


def test_usb_state_parses_features_and_mode():
    text = "usb mounted=1 suspended=0 mouse=1 keyboard=1 touch=0 mode=ios\nOK\n"
    info = parse_usb_info(text)

    assert info.protocol == 1
    assert info.mode == "ios"
    assert info.mounted and info.mouse and info.keyboard
    assert not info.touch
    assert {"usb", "mouse", "keyboard"} <= set(info.features)


def test_preflight_requires_the_hid_interfaces():
    bridge, transport = _bridge(
        replies={"usb": b"usb mounted=0 suspended=0 mouse=1 keyboard=1 touch=0 mode=ios\nOK\n"}
    )

    with pytest.raises(FirmwareCommandError, match="mounted=0"):
        bridge.preflight()
    assert transport.writes == [b"usb\n"]

    ready, _ = _bridge(
        replies={"usb": b"usb mounted=1 suspended=0 mouse=1 keyboard=1 touch=0 mode=ios\nOK\n"}
    )
    info = ready.preflight()
    assert info.mode == "ios"


def test_handshake_reads_v2_hello_without_a_usb_probe():
    bridge, transport = _bridge(
        replies={
            "hello": (
                b"hello proto=2 fw=abc123 board=esp32s3 mode=ios max_line=126 "
                b"features=usb,mouse,keyboard,abs_pointer,abs_click caps=usb.hid,pointer.absolute\n"
                b"OK\n"
            )
        }
    )

    info = bridge.handshake()

    assert info.protocol == 2
    assert info.build == "abc123"
    assert info.board == "esp32s3"
    assert info.mode == "ios"
    assert "abs_pointer" in info.features
    assert "pointer.absolute" in info.caps
    assert info.compatible is True
    assert transport.writes == [b"hello\n"]


def test_handshake_falls_back_to_v1_usb_when_hello_is_unknown():
    bridge, transport = _bridge(
        replies={
            "hello": b"ERR unknown command: hello\nOK\n",
            "usb": b"usb mounted=1 suspended=0 mouse=1 keyboard=1 touch=0 mode=ios\nOK\n",
        }
    )

    info = bridge.handshake()

    assert info.protocol == 1
    assert info.build == "unknown"
    assert info.mode == "ios"
    assert info.mouse is True
    assert info.touch is False
    assert transport.writes == [b"hello\n", b"usb\n"]


def test_handshake_falls_back_when_hello_is_malformed():
    bridge, transport = _bridge(
        replies={
            "hello": b"hello proto=2\nOK\n",
            "usb": b"usb mounted=1 suspended=0 mouse=1 keyboard=1 touch=0 mode=ios\nOK\n",
        }
    )

    assert bridge.handshake().protocol == 1
    assert transport.writes == [b"hello\n", b"usb\n"]


def test_v1_bci_usb_state_parses_without_mouse_or_keyboard_fields():
    info = parse_usb_info("usb mounted=1 suspended=0 bci=1 mode=bci\n")

    assert info.mode == "bci"
    assert info.bci is True
    assert info.mouse is False
    assert info.keyboard is False


def test_a_newer_protocol_is_marked_incompatible():
    info = parse_hello_info(
        "hello proto=3 fw=future board=esp32s3 mode=ios max_line=126 features=usb,mouse\n"
    )

    assert info.protocol == 3
    assert info.compatible is False
