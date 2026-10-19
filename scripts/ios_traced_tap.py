#!/usr/bin/env python3
"""Traced iOS UVC -> PaddleOCR -> pointer precheck -> HID tap. No purchase."""

from __future__ import annotations
import asyncio
import json
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path
from PIL import Image
from artemis.reflex.capture import UvcCapture
from artemis.reflex.control import Esp32HidControl
from artemis.reflex.mouse import PointerSession
from artemis.reflex.vision import RedPointerDetector, RingShapeDetector, changed
from artemis.perception.uvc import UvcSource
from artemis.perception.paddle_ocr import PaddleOcrEngine
from artemis.perception.base import Target


class Trace:
    def __init__(self):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = Path(f"/tmp/artemis-chain/trace_{stamp}.jsonl")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = self.path.open("w", encoding="utf-8")

    def log(self, kind: str, message: str, **data):
        now = datetime.now()
        ts = now.strftime("%H时%M分%S秒.%f微秒")
        row = {"ts": now.isoformat(), "clock": ts, "kind": kind, "message": message, **data}
        line = json.dumps(row, ensure_ascii=False, default=str)
        print(f"[{ts}] {kind}: {message} {json.dumps(data, ensure_ascii=False, default=str)}")
        self.fh.write(line + "\n")
        self.fh.flush()

    def close(self):
        self.fh.close()


class Adapter:
    def __init__(self, capture):
        self.capture = capture

    def grab_latest(self):
        return self.capture.grab()


def detect(detector, ring, frame, trace, near):
    ptr = detector.detect(frame.image, near=near)
    method = "red-ring"
    if ptr is None:
        ptr = ring.detect(frame.image, near=near)
        method = "ring-shape"
    if ptr:
        trace.log(
            "OBSERVE",
            "pointer detected",
            method=method,
            x=round(ptr.x * frame.width, 1),
            y=round(ptr.y * frame.height, 1),
            confidence=round(ptr.confidence, 3),
        )
    else:
        trace.log("OBSERVE", "pointer not detected")
    return ptr


def main():
    trace = Trace()
    capture = UvcCapture(
        "USB Video", size=(1920, 1080), framerate=30, crop=(736, 53, 1185, 1027), timeout_s=12
    )
    control = Esp32HidControl("/dev/cu.usbmodem5B140947461", ack_timeout_s=3.0)
    source = UvcSource(capture, ocr_engine=PaddleOcrEngine())
    detector = RedPointerDetector()
    ring = RingShapeDetector()
    try:
        trace.log("DECIDE", "preflight and read current screen")
        trace.log("OBSERVE", "usb state", state=control.preflight())
        frame = asyncio.run(source.snapshot())
        trace.log(
            "OBSERVE", "UVC frame", health=frame.health, width=frame.width, height=frame.height
        )
        target = asyncio.run(source.locate(frame, Target("闪购", (0, 0, 0, 0), 0, "ocr")))
        trace.log("DECIDE", "locate target", target=target)
        if target is None:
            trace.log("DECIDE", "abort: target not found")
            return
        tx = round((target.box[0] + target.box[2]) / 2 * frame.width)
        ty = round((target.box[1] + target.box[3]) / 2 * frame.height)
        trace.log("DECIDE", "target center in frame pixels", x=tx, y=ty)
        pointer = PointerSession(control, frame_size=(frame.width, frame.height), gain=(1.8, 1.8))
        pointer.connected = True
        trace.log("DECIDE", "pointer.home() for absolute anchor")
        pointer.home()
        trace.log("CONTROL", "pointer.home done", state=pointer.position)
        near = (pointer.position[0] / frame.width, pointer.position[1] / frame.height)
        ptr = detect(detector, ring, capture.grab(), trace, near)
        if ptr is None:
            trace.log("DECIDE", "home pointer not visible; move to visible anchor (120,120)")
            pointer.move(55, 55)
            trace.log("CONTROL", "move to anchor", dx=55, dy=55, state=pointer.position)
            ptr = detect(
                detector,
                ring,
                capture.grab(),
                trace,
                (pointer.position[0] / frame.width, pointer.position[1] / frame.height),
            )
        if ptr is None:
            trace.log("DECIDE", "abort: pointer still not detected; refusing click")
            return
        for step in range(1, 7):
            pointer.observe(ptr.x * frame.width, ptr.y * frame.height)
            ex, ey = tx - pointer.position[0], ty - pointer.position[1]
            err = (ex * ex + ey * ey) ** 0.5
            trace.log(
                "OBSERVE",
                "pointer state",
                x=round(pointer.position[0], 1),
                y=round(pointer.position[1], 1),
                error_px=round(err, 1),
            )
            if err <= 8:
                trace.log("DECIDE", "pointer within 8px; click immediately")
                before = capture.grab()
                pointer.click()
                trace.log("CONTROL", "mouse click", at=pointer.position)
                time.sleep(0.4)
                after = capture.grab()
                ok = changed(before.image, after.image)
                trace.log("OBSERVE", "click verification", changed=ok)
                return
            dx = max(-80, min(80, round(ex / 1.8)))
            dy = max(-80, min(80, round(ey / 1.8)))
            trace.log("DECIDE", "move toward target", dx=dx, dy=dy, error_px=round(err, 1))
            pointer.move(dx, dy)
            trace.log("CONTROL", "move sent", dx=dx, dy=dy, state=pointer.position)
            ptr = detect(
                detector,
                ring,
                capture.grab(),
                trace,
                (pointer.position[0] / frame.width, pointer.position[1] / frame.height),
            )
            if ptr is None:
                trace.log("DECIDE", "abort: pointer lost; refusing click")
                return
    finally:
        control.close()
        trace.close()


if __name__ == "__main__":
    main()
