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
    "MAX_PROTOCOL",
    "MIN_PROTOCOL",
]

MAX_LINE_BYTES = 126
DEFAULT_ACK_TIMEOUT_S = 3.0
MIN_PROTOCOL = 1
MAX_PROTOCOL = 2
_LINE_TOO_LONG = "ERR line too long"
_USB_STATE_RE = re.compile(r"usb mounted=(\d+) suspended=(\d+)(.*)")
_HELLO_RE = re.compile(
    r"hello proto=(\d+) fw=(\S+) board=(\S+) mode=(\w+) max_line=(\d+)"
    r" features=([^\s]+)(?: caps=([^\s]+))?"
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
    caps: frozenset[str] = field(default_factory=frozenset)
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

    @property
    def compatible(self) -> bool:
        return MIN_PROTOCOL <= self.protocol <= MAX_PROTOCOL


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

    def handshake(self, *, timeout: float | None = None) -> FirmwareInfo:
        """Negotiate v2 ``hello``; fall back to a v1 ``usb`` probe.

        Older firmware answers ``ERR unknown command`` (or stays silent), so the
        fallback must not assume any v2-only feature. A malformed hello is treated
        the same way: report v1 limits rather than guessing.
        """

        try:
            text = self.exchange("hello", timeout=1.0 if timeout is None else timeout)
        except (FirmwareCommandError, FirmwareAckTimeout):
            text = ""
        if text:
            try:
                self.info = parse_hello_info(text)
                return self.info
            except FirmwareCommandError:
                pass
        self.info = parse_usb_info(self.exchange("usb"))
        return self.info

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
    """Parse the last ``usb mounted=...`` line into a v1 FirmwareInfo.

    v1 BCI builds omit mouse/keyboard/touch, so unknown fields default to 0
    rather than rejecting the line.
    """

    match = None
    for line in str(text).splitlines():
        match = _USB_STATE_RE.search(line) or match
    if match is None:
        raise FirmwareCommandError(f"firmware did not report a usb state: {text[-200:]!r}")
    mounted, suspended = int(match.group(1)), int(match.group(2))
    fields = {key: value for key, value in re.findall(r"(\w+)=(\w+)", match.group(3))}
    mouse = fields.get("mouse") == "1"
    keyboard = fields.get("keyboard") == "1"
    touch = fields.get("touch") == "1"
    bci = fields.get("bci") == "1"
    mode = fields.get("mode") or ("bci" if bci else "composite" if touch else "ios")
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
        mouse=mouse,
        keyboard=keyboard,
        touch=touch,
        bci=bci,
        raw=text,
    )


def parse_hello_info(text: str) -> FirmwareInfo:
    """Parse a v2 ``hello`` response; raise when the required fields are absent."""

    match = _HELLO_RE.search(str(text))
    if match is None:
        raise FirmwareCommandError(f"firmware did not report a v2 hello: {text[-200:]!r}")
    protocol = int(match.group(1))
    features = frozenset(part for part in match.group(6).split(",") if part)
    caps = frozenset(part for part in (match.group(7) or "").split(",") if part)
    return FirmwareInfo(
        protocol=protocol,
        build=match.group(2),
        board=match.group(3),
        mode=match.group(4),
        max_line=int(match.group(5)),
        features=features,
        caps=caps,
        mounted="usb" in features,
        mouse="mouse" in features,
        keyboard="keyboard" in features,
        touch="touch" in features,
        bci="bci" in features,
        raw=text,
    )
