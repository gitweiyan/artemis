"""R3.8 Android Helper-process fault: force-stop the helper and require fallback."""

from __future__ import annotations

import argparse
import subprocess

from artemis.clients.screen_client_factory import create_screen_client
from artemis.runtime.adb_endpoint import adb_command


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", default="EUWSOBXCZLPFNJF6")
    parser.add_argument("--package", default="com.artemis.helper")
    args = parser.parse_args()
    client = create_screen_client(args.serial, "auto")
    try:
        client.connect()
        client.get_screen_data()
        before = client.active_backend
        subprocess.run(
            adb_command(["-s", args.serial, "shell", "am", "force-stop", args.package]),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        client.get_screen_data()
        after = client.active_backend
        print(
            f"before={before!r} after={after!r} history={client.backend_history}",
            flush=True,
        )
        ok = before == "helper" and after == "uiautomator"
        print("RESULT: PASS" if ok else "RESULT: FAIL", flush=True)
        return 0 if ok else 1
    finally:
        try:
            client.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
