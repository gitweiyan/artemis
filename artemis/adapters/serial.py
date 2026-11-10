"""Raw 8N1 serial transport for the ESP32 UART channel (ADR-009 4.2b).

The port is opened only when the first command is sent. ``O_NONBLOCK`` is used to
get past ``open`` and then cleared so writes wait for the board instead of
raising ``EAGAIN``; reads are bounded by the caller's timeout.
"""

from __future__ import annotations

import os
import select
import time

__all__ = ["SerialTransport"]


class SerialTransport:
    def __init__(self, port: str, *, baudrate: int = 115200) -> None:
        self.port = str(port)
        self.baudrate = int(baudrate)
        self._fd: int | None = None

    def _speed(self):
        import termios

        speed = getattr(termios, f"B{self.baudrate}", None)
        if speed is None:
            raise ValueError(f"unsupported serial baud rate {self.baudrate!r}")
        return speed

    def _ensure_open(self) -> int:
        if self._fd is not None:
            return self._fd
        import fcntl
        import termios

        fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            fcntl.fcntl(fd, fcntl.F_SETFL, fcntl.fcntl(fd, fcntl.F_GETFL) & ~os.O_NONBLOCK)
            _iflag, _oflag, _cflag, _lflag, _ispeed, _ospeed, cc = termios.tcgetattr(fd)
            speed = self._speed()
            cc = list(cc)
            cc[termios.VMIN] = 0
            cc[termios.VTIME] = 0
            termios.tcsetattr(
                fd,
                termios.TCSANOW,
                [0, 0, termios.CS8 | termios.CREAD | termios.CLOCAL, 0, speed, speed, cc],
            )
            termios.tcflush(fd, termios.TCIOFLUSH)
        except (OSError, ValueError):
            os.close(fd)
            raise
        self._fd = fd
        return fd

    def write(self, data: bytes) -> None:
        fd = self._ensure_open()
        view = memoryview(bytes(data))
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError(f"serial write to {self.port} made no progress")
            view = view[written:]

    def read(self, timeout_s: float) -> bytes:
        if self._fd is None:
            return b""
        try:
            readable, _, _ = select.select([self._fd], [], [], max(0.0, float(timeout_s)))
            if not readable:
                return b""
            return os.read(self._fd, 4096)
        except BlockingIOError:
            return b""

    def close(self) -> None:
        if self._fd is None:
            return
        try:
            os.close(self._fd)
        finally:
            self._fd = None

    def __enter__(self) -> SerialTransport:
        self._ensure_open()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
