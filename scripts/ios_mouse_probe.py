#!/usr/bin/env python3
"""Probe ESP32 mouse HID movement and click on iPhone — no UVC.

This script talks only to the ESP32 UART:
- ``--dx/--dy`` choose the relative movement (repeat with ``--repeat``);
- ``--click`` sends a left click at the current pointer position;
- every command waits for the firmware's ``OK`` ack and is timed.

It does not open a camera, ffmpeg, or any screen capture. Watch the iPhone
yourself; the log proves whether the board accepted each command.

Usage:

    cd /Users/xf/Documents/code/artemis
    .venv/bin/python scripts/ios_mouse_probe.py \
        --port /dev/cu.usbmodem5B140947461 \
        --dx 100 --dy 0 --repeat 3 --click

Output: /tmp/esp32-ios-mouse/<timestamp>/run.log and report.json
Exit: 0 = every requested command was acknowledged, 1 = preflight/command error.
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from artemis.reflex.control import Esp32HidControl


def resolve_port(value: str | None) -> str:
    if value:
        return value
    env = os.environ.get("ARTEMIS_ESP32_PORT", "").strip()
    if env:
        return env
    ports = sorted(glob.glob("/dev/cu.usbmodem*"))
    if len(ports) == 1:
        return ports[0]
    if ports:
        raise SystemExit("multiple /dev/cu.usbmodem* ports; pass --port: " + ", ".join(ports))
    raise SystemExit("no ESP32 port found; pass --port or set ARTEMIS_ESP32_PORT")


def hid_delta(value: str) -> int:
    number = int(value)
    if not -10000 <= number <= 10000:
        raise argparse.ArgumentTypeError("dx/dy must be in -10000..10000")
    return number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Probe ESP32 mouse HID movement/click on iPhone (UART only, no UVC)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--port", help="ESP32 UART port (default: env or auto)")
    parser.add_argument("--dx", type=hid_delta, required=True, help="relative mouse dx")
    parser.add_argument("--dy", type=hid_delta, required=True, help="relative mouse dy")
    parser.add_argument(
        "--repeat", type=int, default=1, help="send the same dx/dy N times (default 1)"
    )
    parser.add_argument(
        "--click", action="store_true", help="left-click at the current pointer position"
    )
    parser.add_argument("--settle", type=float, default=0.2, help="seconds between commands")
    parser.add_argument("--out", help="output directory (default /tmp/esp32-ios-mouse/<timestamp>)")
    args = parser.parse_args(argv)

    if not 1 <= args.repeat <= 50:
        parser.error("--repeat must be 1..50")
    if args.dx == 0 and args.dy == 0 and not args.click:
        parser.error("dx=dy=0 with no --click would do nothing")

    out_dir = (
        Path(args.out)
        if args.out
        else Path("/tmp/esp32-ios-mouse") / datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("ios_mouse_probe")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    file_handler = logging.FileHandler(out_dir / "run.log", encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(stream)
    logger.addHandler(file_handler)

    port = resolve_port(args.port)
    logger.info("port=%s", port)
    logger.info("dx=%d dy=%d repeat=%d click=%s", args.dx, args.dy, args.repeat, args.click)
    logger.info("output=%s", out_dir)

    report: dict = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "port": port,
        "dx": args.dx,
        "dy": args.dy,
        "repeat": args.repeat,
        "click_requested": bool(args.click),
        "moves": [],
        "ok": False,
    }

    control = Esp32HidControl(port, ack_timeout_s=5.0)
    try:
        logger.info("preflight: querying usb state...")
        state = control.preflight()
        report["preflight"] = state
        logger.info("preflight ok: %s", state)

        for index in range(1, args.repeat + 1):
            started = time.monotonic()
            logger.info("[move %d/%d] move %d %d", index, args.repeat, args.dx, args.dy)
            control.mouse_move(args.dx, args.dy)
            elapsed = (time.monotonic() - started) * 1000.0
            move = {"index": index, "dx": args.dx, "dy": args.dy, "ack_ms": round(elapsed, 1)}
            report["moves"].append(move)
            logger.info("    ack in %.1f ms", elapsed)
            time.sleep(max(0.0, args.settle))

        if args.click:
            started = time.monotonic()
            logger.info("[click] left click at current pointer")
            control.mouse_click()
            elapsed = (time.monotonic() - started) * 1000.0
            report["click"] = {"requested": True, "ack_ms": round(elapsed, 1)}
            logger.info("    ack in %.1f ms", elapsed)

        final = control._send_with_response("usb")
        report["final_usb"] = final.strip().splitlines()[-2:]
        logger.info("final usb: %s", report["final_usb"])
        report["ok"] = True
        (out_dir / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        logger.info("report: %s", out_dir / "report.json")
        logger.info("RESULT: commands acknowledged — watch the iPhone for pointer movement/click")
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        (out_dir / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        logger.error("probe failed: %s", exc)
        logger.error("report: %s", out_dir / "report.json")
        return 1
    finally:
        try:
            control.close()
        except Exception:  # noqa: BLE001 - cleanup must not hide the real error
            logger.exception("control.close failed")


if __name__ == "__main__":
    raise SystemExit(main())
