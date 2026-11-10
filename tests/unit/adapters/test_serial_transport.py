"""Raw serial transport smoke test over a pseudo-terminal."""

from __future__ import annotations

import os
import pty
import sys

import pytest

from artemis.adapters.serial import SerialTransport

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="pty is POSIX-only")


def test_write_and_read_roundtrip_on_a_pty():
    master, slave = pty.openpty()
    transport = SerialTransport(os.ttyname(slave))
    try:
        transport.write(b"move 1 2\n")
        assert os.read(master, 64) == b"move 1 2\n"

        os.write(master, b"OK\n")
        assert transport.read(0.5) == b"OK\n"
    finally:
        transport.close()
        os.close(master)
        os.close(slave)
