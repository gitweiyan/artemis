#!/usr/bin/env python3
"""Probe TypeSafe Jev Box localization on one local screenshot.

The API key is read from TYPESAFE_API_KEY or ~/.config/typesafe/key (chmod 600).
Images/Box are Dreamer-ready and require the danger-zone header; Box needs
jev-preview or jev-1.14.0. Uses requests so the local VPN proxy env is honored.

    .venv/bin/python scripts/typesafe_box_probe.py \
        --image /tmp/artemis-chain/ad_x_near.png \
        --prompt "the white X close button near the bottom center" \
        --expected 225,737
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageOps

API = "https://api.typesafe.ai/v1/systemone"
MAX_SIDE = 1568
MIN_SIDE = 280


def load_key() -> str:
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if key:
        return key
    path = Path.home() / ".config" / "typesafe" / "key"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    raise SystemExit(
        "missing TYPESAFE_API_KEY (or ~/.config/typesafe/key). "
        "Set it locally; do not paste the key into chat."
    )


def prepare_image(path: Path) -> tuple[bytes, tuple[int, int]]:
    with Image.open(path) as original:
        img = ImageOps.exif_transpose(original).convert("RGB")
    if min(img.size) < MIN_SIDE:
        raise SystemExit(f"image {img.size} is below the {MIN_SIDE}px API minimum")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    buffer = BytesIO()
    img.save(buffer, "JPEG", quality=90)
    return buffer.getvalue(), img.size


def main() -> int:
    parser = argparse.ArgumentParser(description="TypeSafe Jev Box probe")
    parser.add_argument("--image", required=True)
    parser.add_argument("--prompt", default="the white X close button near the bottom center")
    parser.add_argument("--model", default="jev-preview")
    parser.add_argument("--expected", help="optional x,y for a quick hit check")
    parser.add_argument("--out", default="/tmp/artemis-chain/typesafe_box")
    args = parser.parse_args()

    key = load_key()
    image_path = Path(args.image)
    data, size = prepare_image(image_path)
    width, height = size
    ref = f"probe-{int(time.time())}"
    body = {
        "model": args.model,
        "state": {"screen": ref},
        "assets": {ref: {"type": "image", "part": "screen"}},
        "questions": {
            "target": {
                "type": "box",
                "instructions": f"{args.prompt} in `screen`",
            }
        },
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "TypeSafe-Danger-Zone-Images": "true",
    }
    started = time.monotonic()
    response = requests.post(
        API,
        headers=headers,
        data={"request": json.dumps(body)},
        files={"screen": ("screen.jpg", data, "image/jpeg")},
        timeout=90,
    )
    elapsed = (time.monotonic() - started) * 1000.0
    if response.status_code != 200:
        raise SystemExit(f"HTTP {response.status_code}: {response.text[:1200]}")
    payload = response.json()
    answer = payload["answers"]["target"]
    box = answer["box"]
    px = (
        round(box["left"] * width),
        round(box["top"] * height),
        round(box["right"] * width),
        round(box["bottom"] * height),
    )
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    annotated = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(annotated)
    draw.rectangle(px, outline=(0, 255, 255), width=4)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    png = out / f"box_{stamp}.png"
    js = out / f"box_{stamp}.json"
    annotated.save(png)
    result = {
        "image": str(image_path),
        "image_size": [width, height],
        "model": payload.get("model"),
        "prompt": args.prompt,
        "box": box,
        "pixels": px,
        "confidence": answer.get("confidence"),
        "latency_ms": round(elapsed),
        "usage": payload.get("usage"),
        "annotated": str(png),
    }
    if args.expected:
        ex, ey = (float(v) for v in args.expected.split(","))
        result["expected"] = [ex, ey]
        result["hit"] = px[0] <= ex <= px[2] and px[1] <= ey <= px[3]
    js.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
