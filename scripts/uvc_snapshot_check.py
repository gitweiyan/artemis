#!/usr/bin/env python3
"""Verify that one on-demand UVC snapshot works. Nothing else.

This script does exactly one thing: run ffmpeg for a single frame from the UVC
capture card, save it, and report whether the frame is usable (not black/empty).
It does not touch the ESP32, mouse, keyboard, OCR, or any automation.

    .venv/bin/python scripts/uvc_snapshot_check.py

    # custom output / device / crop
    .venv/bin/python scripts/uvc_snapshot_check.py \
        --device "USB Video" --out /tmp/uvc_check/frame.png

Exit codes: 0 = valid non-black frame, 1 = black/no-signal frame, 2 = capture error.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

from PIL import Image


def parse_crop(value: str) -> tuple[int, int, int, int]:
    parts = [int(part.strip()) for part in value.split(",")]
    if len(parts) != 4 or parts[0] >= parts[2] or parts[1] >= parts[3]:
        raise argparse.ArgumentTypeError("crop must be left,top,right,bottom")
    return parts[0], parts[1], parts[2], parts[3]


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify one UVC snapshot")
    parser.add_argument("--device", default="USB Video")
    parser.add_argument("--size", default="1920x1080")
    parser.add_argument("--framerate", type=int, default=30)
    parser.add_argument("--crop", type=parse_crop, default=(736, 53, 1185, 1027))
    parser.add_argument("--pixel-format", default="nv12")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--out", default="/tmp/uvc_check/frame.png")
    args = parser.parse_args()

    width, height = (int(part) for part in args.size.lower().split("x"))
    left, top, right, bottom = args.crop
    crop = f"{right - left}:{bottom - top}:{left}:{top}"
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "avfoundation",
        "-pixel_format",
        args.pixel_format,
        "-framerate",
        str(args.framerate),
        "-video_size",
        f"{width}x{height}",
        "-i",
        args.device,
        "-frames:v",
        "1",
        "-vf",
        f"crop={crop}",
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "-",
    ]

    started = time.monotonic()
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=args.timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 2
    elapsed_ms = (time.monotonic() - started) * 1000.0
    if result.returncode != 0 or not result.stdout:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"ffmpeg exit {result.returncode}",
                    "stderr": result.stderr.decode("utf-8", "replace")[-800:],
                    "ms": round(elapsed_ms),
                },
                ensure_ascii=False,
            )
        )
        return 2

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(result.stdout)
    try:
        with Image.open(out) as image:
            image.load()
            size = image.size
            rgb = image.convert("RGB")
            small = rgb.resize((64, 64))
            raw = small.tobytes()
            pixels = [tuple(raw[i : i + 3]) for i in range(0, len(raw), 3)]
    except Exception as exc:  # noqa: BLE001 - report a bad frame, don't crash
        print(json.dumps({"ok": False, "error": f"decode failed: {exc}"}))
        return 2

    mean = sum(sum(pixel) / 3 for pixel in pixels) / len(pixels)
    nonblack = sum(1 for pixel in pixels if max(pixel) > 8) / len(pixels)
    status = "OK" if nonblack > 0.01 and mean > 2.0 else "BLACK"
    summary = {
        "ok": status == "OK",
        "status": status,
        "path": str(out),
        "size": list(size),
        "bytes": len(result.stdout),
        "mean_brightness": round(mean, 2),
        "nonblack_ratio": round(nonblack, 3),
        "capture_ms": round(elapsed_ms),
        "device": args.device,
        "pixel_format": args.pixel_format,
        "crop": list(args.crop),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if status != "OK":
        print("Hint: screen off/locked, no HDMI signal, or the capture card is busy.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
