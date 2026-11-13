"""R3.8 Android Helper/Tunnel fault injection.

Starts an auto helper-first screen client, gets one helper observation, removes
the helper's adb forward, then verifies the next observation degrades to
UIAutomator2 (not a Controller fallback). The helper service itself stays up.
"""

from __future__ import annotations

import argparse
import subprocess

from artemis.clients.screen_client_factory import create_screen_client
from artemis.runtime.adb_endpoint import adb_command


def _forward_port(serial: str) -> int | None:
    result = subprocess.run(
        adb_command(["-s", serial, "forward", "--list"]),
        capture_output=True,
        text=True,
        timeout=10,
    )
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] == serial and parts[2] == "tcp:18888":
            try:
                return int(parts[1].split(":", 1)[1])
            except (IndexError, ValueError):
                return None
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", default="EUWSOBXCZLPFNJF6")
    args = parser.parse_args()
    client = create_screen_client(args.serial, "auto")
    try:
        client.connect()
        first = client.get_screen_data()
        port = _forward_port(args.serial)
        print(
            f"HELPER_READY backend={client.active_backend!r} port={port}"
            f" width={getattr(first, 'width', None)} height={getattr(first, 'height', None)}",
            flush=True,
        )
        if port is None:
            print("RESULT: FAIL no helper forward found", flush=True)
            return 2
        subprocess.run(
            adb_command(["-s", args.serial, "forward", "--remove", f"tcp:{port}"]),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        second = client.get_screen_data()
        print(
            f"FAULT_DETECTED removed_forward={port} backend={client.active_backend!r}"
            f" history={client.backend_history} width={getattr(second, 'width', None)}",
            flush=True,
        )
        ok = client.active_backend == "uiautomator"
        print("RESULT: PASS" if ok else "RESULT: FAIL fallback backend not uiautomator", flush=True)
        return 0 if ok else 1
    finally:
        try:
            client.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
