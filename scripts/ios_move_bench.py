#!/usr/bin/env python3
"""Bench the iOS pointer's phased travel: move to a target, measure, never click.

The bench drives the same verified loop a tap uses (``SessionTapController.travel_verified``),
so its numbers describe production motion: how many frame observations a travel needed,
which phases ran, how far the pointer actually landed, and how often the colour detector
missed the corridor. It never presses the button, so it is safe on any screen.

    ARTEMIS_ESP32_PORT=/dev/cu.usbmodem5B140947461 \\
        .venv/bin/python scripts/ios_move_bench.py --target 224,811 --runs 10

Exits 1 when the hit rate (final error <= --tolerance) is below 90 % or the median
observation count exceeds 4 — the two numbers the move plan is judged on.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import statistics
import time

from artemis.devices.ios_uvc import build_ios_uvc_session
from artemis.drivers.ios.config import IosRuntimeConfig
from artemis.drivers.ios.ios_uvc_hid_driver import IOSUvcHidDriver
from artemis.reflex.capture import UvcCapture
from artemis.reflex.control import Esp32HidControl
from artemis.reflex.motion import Phase
from artemis.reflex.vision import BluePointerDetector, CompositePointerDetector

#: The move plan's acceptance: a travel must land this close, with this many observations.
HIT_RATE_FLOOR = 0.90
MEDIAN_OBSERVATIONS_CEILING = 4


def _target(raw: str) -> tuple[float, float]:
    try:
        x, y = (float(part) for part in raw.split(","))
    except ValueError as exc:
        raise SystemExit(f"--target must be 'x,y' in capture-frame pixels, got {raw!r}") from exc
    return x, y


def _parse(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, help="x,y target in capture-frame pixels")
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument(
        "--port", default=os.environ.get("ARTEMIS_ESP32_PORT", ""), help="ESP32 serial port"
    )
    parser.add_argument("--tolerance", type=float, default=8.0, help="hit threshold, frame px")
    args = parser.parse_args(argv)
    args.target = _target(args.target)
    if not args.port:
        raise SystemExit("give --port or set ARTEMIS_ESP32_PORT")
    if args.runs < 1:
        raise SystemExit("--runs must be >= 1")
    return args


def _gains(controller) -> dict[str, list[float]]:
    calibration = getattr(controller, "motion_calibration", None)
    if calibration is None:
        return {}
    return {phase.value: [round(value, 3) for value in calibration.gain(phase)] for phase in Phase}


async def _run(args: argparse.Namespace) -> int:
    ios = IosRuntimeConfig.from_env({**os.environ, "ARTEMIS_ESP32_PORT": args.port})
    capture = UvcCapture(
        ios.uvc_device,
        size=ios.uvc_size,
        framerate=30,
        crop=ios.uvc_crop,
        timeout_s=12.0,
    )
    control = Esp32HidControl(ios.esp32_port, ack_timeout_s=3.0)
    session = build_ios_uvc_session(
        capture,
        control,
        frame_size=ios.frame_size,
        screen_size=ios.screen_size,
        pointer_gain=ios.pointer_gain,
    )
    driver = IOSUvcHidDriver(session)
    await driver.connect()
    controller = session.control[0].controller
    # Grade with the rig's own pointer model (blue ring only): a red UI badge must not
    # count as the pointer in the measurement either.
    detector = CompositePointerDetector([BluePointerDetector()])
    frame_width, frame_height = ios.frame_size
    target_x, target_y = args.target

    runs: list[dict] = []
    for index in range(args.runs):
        session.pointer.home()  # every run starts from the same known origin
        started = time.monotonic()
        result = controller.travel_verified(target_x, target_y)
        elapsed_ms = (time.monotonic() - started) * 1000.0
        motion = getattr(result, "motion", None)
        # Grade with an independent reading, taken while the pointer is still drawn: a
        # grab right after the travel sees an already-faded pointer ~half the time. A
        # symmetric out-and-back nudge redraws it and cancels itself, so what is detected
        # is the travel's own endpoint, not a shifted one.
        session.pointer.move(60, 60)
        session.pointer.move(-60, -60)
        frame = capture.grab()
        pointer = detector.detect(
            frame.image, near=(target_x / frame_width, target_y / frame_height)
        )
        measured = None
        if pointer is not None:
            measured = math.hypot(
                pointer.x * frame_width - target_x, pointer.y * frame_height - target_y
            )
        error = None if measured is None else round(measured, 2)
        hit = error is not None and error <= args.tolerance
        record = {
            "run": index + 1,
            "hit": hit,
            "ok": bool(result.ok),
            "incident": result.incident,
            "final_error_px": error,
            "controller_error_px": None
            if result.error_px is None
            else round(float(result.error_px), 2),
            "elapsed_ms": round(elapsed_ms, 1),
            "segments": getattr(motion, "segments", 0),
            "observations": getattr(motion, "observations", 0),
            "phases": list(getattr(motion, "phases", []) or []),
            "global_recoveries": getattr(motion, "global_recoveries", 0),
            "gains": _gains(controller),
        }
        print(json.dumps(record, ensure_ascii=False), flush=True)
        runs.append(record)

    errors = [record["final_error_px"] for record in runs if record["final_error_px"] is not None]
    observations = [record["observations"] for record in runs]
    hits = sum(1 for record in runs if record["hit"])
    hit_rate = hits / len(runs)
    summary = {
        "runs": len(runs),
        "target": [target_x, target_y],
        "tolerance_px": args.tolerance,
        "hit_rate": round(hit_rate, 3),
        "median_error_px": round(statistics.median(errors), 2) if errors else None,
        "misses": len(runs) - hits,
        "median_observations": statistics.median(observations),
        "median_segments": statistics.median([record["segments"] for record in runs]),
        "total_global_recoveries": sum(record["global_recoveries"] for record in runs),
    }
    print(json.dumps({"summary": summary}, ensure_ascii=False), flush=True)
    passed = (
        hit_rate >= HIT_RATE_FLOOR and summary["median_observations"] <= MEDIAN_OBSERVATIONS_CEILING
    )
    return 0 if passed else 1


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run(_parse(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
