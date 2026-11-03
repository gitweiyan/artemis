#!/usr/bin/env python3
"""Aim the iPhone pointer with on-demand UVC frames and a red-ring detector.

No 30 fps stream: each correction grabs one frame, finds the red AssistiveTouch
ring, computes the residual to ``--target``, and sends a bounded relative move.
The click is optional and only happens after the pointer is within tolerance.

    .venv/bin/python scripts/ios_pointer_servo.py \
        --port /dev/cu.usbmodem5B140947461 \
        --target 380,235 --gain 1.8

Add ``--click`` to click after convergence.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from artemis.reflex.capture import UvcCapture
from artemis.reflex.control import Esp32HidControl
from artemis.reflex.mouse import PointerSession
from artemis.reflex.vision import RedPointerDetector, changed


def main() -> int:
    parser = argparse.ArgumentParser(description="On-demand UVC pointer servo")
    parser.add_argument("--port", default="/dev/cu.usbmodem5B140947461")
    parser.add_argument("--target", required=True, help="x,y in the 449x974 crop")
    parser.add_argument("--gain", type=float, default=1.8)
    parser.add_argument("--tolerance", type=float, default=8.0)
    parser.add_argument("--steps", type=int, default=6)
    parser.add_argument("--max-step", type=int, default=80)
    parser.add_argument("--click", action="store_true")
    parser.add_argument(
        "--require-detect",
        action="store_true",
        help="click only if the red ring was actually detected on the latest frame",
    )
    parser.add_argument(
        "--no-home",
        action="store_true",
        help="do not Cmd+H; aim from the current app screen",
    )
    parser.add_argument("--out", default="/tmp/artemis-chain/servo")
    args = parser.parse_args()
    tx, ty = (float(v) for v in args.target.split(","))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    control = Esp32HidControl(args.port, ack_timeout_s=5.0)
    session = PointerSession(
        control,
        frame_size=(449, 974),
        gain=(args.gain, args.gain),
    )
    capture = UvcCapture(
        "USB Video",
        size=(1920, 1080),
        framerate=30,
        crop=(736, 53, 1185, 1027),
        timeout_s=12,
    )
    detector = RedPointerDetector()
    final = None
    error = None
    last_found = False
    try:
        print("connect:", control.preflight())
        if args.no_home:
            session.connected = True
            session.home()  # pointer-only home; does not leave the app
        else:
            session.connect()
        for step in range(1, args.steps + 1):
            frame = capture.grab()
            (out / f"step{step:02d}.png").write_bytes(frame.image)
            found = detector.detect(
                frame.image,
                near=(session.position[0] / 449.0, session.position[1] / 974.0),
            )
            last_found = found is not None
            if found is not None:
                session.observe(found.x * 449.0, found.y * 974.0)
                print(
                    f"step {step}: pointer=({session.position[0]:.1f},{session.position[1]:.1f}) "
                    f"conf={found.confidence:.2f}"
                )
            else:
                print(f"step {step}: pointer not found; using state {session.position}")
            error = ((tx - session.position[0]) ** 2 + (ty - session.position[1]) ** 2) ** 0.5
            print(f"  target=({tx:.0f},{ty:.0f}) error={error:.1f}px")
            if error <= args.tolerance:
                final = session.position
                break
            dx = round((tx - session.position[0]) / session.gain_x)
            dy = round((ty - session.position[1]) / session.gain_y)
            dx = max(-args.max_step, min(args.max_step, dx))
            dy = max(-args.max_step, min(args.max_step, dy))
            if dx == 0 and dy == 0:
                break
            try:
                session.move(dx, dy)
            except RuntimeError as exc:
                # A transient UART "line too long" can arrive late; re-observe on
                # the next loop instead of blindly resending a move that may have
                # already been applied.
                print(f"  move error, will re-observe: {exc}")
                time.sleep(0.15)
                continue
            time.sleep(0.25)

        if final is None:
            frame = capture.grab()
            (out / "final.png").write_bytes(frame.image)
            found = detector.detect(frame.image)
            last_found = found is not None
            if found is not None:
                session.observe(found.x * 449.0, found.y * 974.0)
            error = ((tx - session.position[0]) ** 2 + (ty - session.position[1]) ** 2) ** 0.5
            print(
                f"final: pointer=({session.position[0]:.1f},{session.position[1]:.1f}) "
                f"error={error:.1f}px"
            )
        if args.click:
            if (
                error is not None
                and error <= args.tolerance
                and (not args.require_detect or last_found)
            ):
                before = capture.grab()
                session.tap(*session.position)
                time.sleep(0.4)
                after = capture.grab()
                print("click changed:", changed(before.image, after.image))
            else:
                print("not clicking: pointer not confirmed at the target")
                return 1
        return 0 if error is not None and error <= args.tolerance else 1
    finally:
        control.close()


if __name__ == "__main__":
    raise SystemExit(main())
