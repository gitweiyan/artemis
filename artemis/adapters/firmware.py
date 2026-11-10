"""Host-side ESP32 firmware bridge (ADR-009 Phase 4.2a).

This module owns the v1 line-protocol facts — 115200 line framing, one command in
flight, ``OK``/``ERR`` parsing, the 126-byte line limit, ACK timeout and resync —
without opening a serial device itself. A transport implements the byte exchange;
tests use an in-memory transport, production can bind the existing serial helper.

ACK is only a firmware/protocol fact (ADR-004/009): it never proves that the target
OS accepted or that the UI changed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
import threading
import time
from typing import Protocol

__all__ = [
    "FirmwareAckTimeout",
    "FirmwareBridge",
    "FirmwareCommandError",
    "FirmwareInfo",
    "FirmwareLineTooLong",
    "LineTransport",
]

MAX_LINE_BYTES = 126
DEFAULT_ACK_TIMEOUT_S = 3.0
_LINE_TOO_LONG = "ERR line too long"
_USB_STATE_RE = re.compile(
    r"usb mounted=(\d+) suspended=(\d+) mouse=(\d+) keyboard=(\d+) touch=(\d+)"
    r"(?: bci=(\d+))?(?: mode=(\w+))?"
)


class FirmwareError(RuntimeError):
    """Base class for firmware/protocol failures."""


class FirmwareCommandError(FirmwareError):
    """The firmware answered ``ERR`` or ``usage:`` for a command."""


class FirmwareAckTimeout(FirmwareError):
    """No ``OK`` arrived within the ACK budget."""


class FirmwareLineTooLong(FirmwareError):
    """A command exceeds the firmware's 126-byte line limit (including newline)."""


class LineTransport(Protocol):
    def write(self, data: bytes) -> None: ...

    def read(self, timeout_s: float) -> bytes: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class FirmwareInfo:
    protocol: int = 1
    build: str = "unknown"
    board: str = "unknown"
    mode: str = "unknown"
    max_line: int = MAX_LINE_BYTES
    features: frozenset[str] = field(default_factory=frozenset)
    mounted: bool = False
    suspended: bool = False
    mouse: bool = False
    keyboard: bool = False
    touch: bool = False
    bci: bool = False
    raw: str = ""

    @property
    def variant(self) -> str:
        return self.mode


class FirmwareBridge:
    """One serial command channel with the firmware's v1 flow control."""

    def __init__(
        self,
        transport: LineTransport,
        *,
        ack_timeout_s: float = DEFAULT_ACK_TIMEOUT_S,
        max_line: int = MAX_LINE_BYTES,
    ) -> None:
        self.transport = transport
        self.ack_timeout_s = float(ack_timeout_s)
        self.max_line = int(max_line)
        self._lock = threading.Lock()

    def exchange(self, command: str, *, timeout: float | None = None) -> str:
        """Send one command and wait for ``OK``; retry a line-too-long resync once."""

        try:
            return self._exchange_once(command, timeout=timeout)
        except FirmwareCommandError as exc:
            if _LINE_TOO_LONG not in str(exc):
                raise
            self._resync()
            return self._exchange_once(command, timeout=timeout)

    def send(self, command: str, *, timeout: float | None = None) -> str:
        return self.exchange(command, timeout=timeout)

    def _line(self, command: str) -> bytes:
        if not command or "\n" in command or "\r" in command:
            raise ValueError("a firmware command must be one non-empty line")
        line = (command + "\n").encode("ascii")
        if len(line) > self.max_line:
            raise FirmwareLineTooLong(
                f"command is {len(line)} bytes; firmware line limit is {self.max_line}"
            )
        return line

    def _exchange_once(self, command: str, *, timeout: float | None) -> str:
        line = self._line(command)
        wait_for = self.ack_timeout_s if timeout is None else float(timeout)
        with self._lock:
            self.transport.write(line)
            deadline = time.monotonic() + wait_for
            data = b""
            while time.monotonic() < deadline:
                chunk = self.transport.read(min(0.05, max(0.0, deadline - time.monotonic())))
                if not chunk:
                    continue
                data += chunk
                text = data.decode("utf-8", "replace")
                lines = [part.strip() for part in text.splitlines()]
                if any(part == "OK" for part in lines):
                    errors = [
                        part
                        for part in lines
                        if part.startswith("ERR ") or part.startswith("usage:")
                    ]
                    if errors:
                        raise FirmwareCommandError(
                            f"firmware command {command!r} failed: {errors[0]}"
                        )
                    return text
            raise FirmwareAckTimeout(
                f"firmware command {command!r} got no OK within {wait_for:.1f}s;"
                f" response={data[-300:]!r}"
            )

    def _resync(self) -> None:
        """Terminate an orphan fragment in the firmware line parser."""

        try:
            self.transport.write(b"\n")
        except (OSError, RuntimeError, ValueError, AttributeError):
            return

    def preflight(self) -> FirmwareInfo:
        """Read the ``usb`` state; v1 exposes features but no protocol/build/board."""

        text = self.exchange("usb")
        info = parse_usb_info(text)
        problems: list[str] = []
        if not info.mounted:
            problems.append("mounted=0 (check the OTG cable and the Allow prompt)")
        if info.suspended:
            problems.append("suspended=1 (unlock the phone or disable auto-lock)")
        if not info.mouse:
            problems.append("mouse=0 (the HID mouse interface is not ready)")
        if not info.keyboard:
            problems.append("keyboard=0 (the HID keyboard interface is not ready)")
        if problems:
            raise FirmwareCommandError("ESP32 HID not ready: " + "; ".join(problems))
        return info

    def close(self) -> None:
        self.transport.close()


def parse_usb_info(text: str) -> FirmwareInfo:
    """Parse the last ``usb mounted=...`` line into a v1 FirmwareInfo."""

    match = None
    for line in str(text).splitlines():
        match = _USB_STATE_RE.search(line) or match
    if match is None:
        raise FirmwareCommandError(f"firmware did not report a usb state: {text[-200:]!r}")
    mounted, suspended, mouse, keyboard, touch = (int(match.group(i)) for i in range(1, 6))
    bci = int(match.group(6) or 0)
    mode = match.group(7) or ("composite" if touch else "ios")
    features: set[str] = set()
    if mounted:
        features.add("usb")
    if suspended:
        features.add("suspended")
    if mouse:
        features.add("mouse")
    if keyboard:
        features.add("keyboard")
    if touch:
        features.add("touch")
    if bci:
        features.add("bci")
    return FirmwareInfo(
        mode=mode,
        max_line=MAX_LINE_BYTES,
        features=frozenset(features),
        mounted=bool(mounted),
        suspended=bool(suspended),
        mouse=bool(mouse),
        keyboard=bool(keyboard),
        touch=bool(touch),
        bci=bool(bci),
        raw=text,
    )
