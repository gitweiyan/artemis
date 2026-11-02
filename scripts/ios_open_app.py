#!/usr/bin/env python3
"""Open an iOS app through Spotlight, optionally verifying a UVC screen change."""
from __future__ import annotations

import argparse
import json
import os

from artemis.reflex.capture import UvcCapture
from artemis.reflex.control import Esp32HidControl
from artemis.reflex.ios_apps import IOSAppLauncher


def main() -> int:
    parser = argparse.ArgumentParser(description="Open an iOS app via Spotlight")
    parser.add_argument("--app", required=True, help="app name, e.g. taobao")
    parser.add_argument("--query", default=None, help="Spotlight query (default: --app)")
    parser.add_argument("--port", default=os.environ.get("ARTEMIS_ESP32_PORT", ""))
    parser.add_argument("--wait", type=float, default=0.7)
    parser.add_argument("--no-capture", action="store_true")
    args = parser.parse_args()
    if not args.port:
        parser.error("--port or ARTEMIS_ESP32_PORT is required")

    control = Esp32HidControl(args.port)
    capture = None if args.no_capture else UvcCapture(
        "USB Video", size=(1920, 1080), framerate=30,
        crop=(736, 53, 1185, 1027), timeout_s=12,
    )
    try:
        control.preflight()
        result = IOSAppLauncher(control, wait_s=args.wait).open_app(
            args.app, query=args.query, capture=capture
        )
        print(json.dumps({
            "ok": result.ok,
            "app": result.app,
            "query": result.query,
            "method": result.method,
            "error": result.error,
            "verified": capture is not None,
        }, ensure_ascii=False))
        return 0 if result.ok else 1
    finally:
        control.close()


if __name__ == "__main__":
    raise SystemExit(main())
