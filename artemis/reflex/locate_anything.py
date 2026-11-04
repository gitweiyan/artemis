"""Pointer detection through the local LocateAnything HTTP service.

The service (``base + lora_ptr5``) answers ``POST /predict`` with a point for a text
prompt; its own demo prompts are the AssistiveTouch pointer ("the blue circular mouse
pointer"). It is a *first* detector: the colour/shape chain still runs when the
service is down, slow, or returns nothing.

The frame is sent as a data URL; width/height 0 lets the service read the image size.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.request

from artemis.reflex.vision import Pointer

__all__ = ["LocateAnythingDetector", "DEFAULT_LOCATE_URL", "DEFAULT_LOCATE_PROMPT"]

DEFAULT_LOCATE_URL = "http://127.0.0.1:7861"
DEFAULT_LOCATE_PROMPT = "the blue circular mouse pointer"


def _data_url(image: bytes) -> str:
    mime = "image/png"
    if image[:3] == b"\xff\xd8\xff":
        mime = "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(image).decode('ascii')}"


class LocateAnythingDetector:
    """A ``detect(image, near)`` detector backed by the LocateAnything service."""

    def __init__(
        self,
        url: str = DEFAULT_LOCATE_URL,
        *,
        prompt: str = DEFAULT_LOCATE_PROMPT,
        timeout_s: float = 4.0,
        cooldown_s: float = 60.0,
    ):
        self.url = str(url).rstrip("/")
        self.prompt = str(prompt)
        self.timeout_s = max(0.1, float(timeout_s))
        self.cooldown_s = max(0.0, float(cooldown_s))
        self._failures = 0
        self._skip_until = 0.0

    def locate(self, image: bytes, prompt: str) -> tuple[float, float] | None:
        """The service's 0..1 point for ``prompt``, or None while unavailable."""
        # Two consecutive failures mean the service is not usable right now; skipping it
        # keeps every observation from paying two timeouts in a row.
        if time.monotonic() < self._skip_until:
            return None
        try:
            payload = json.dumps(
                {"image": _data_url(image), "prompt": str(prompt), "w": 0, "h": 0}
            ).encode("utf-8")
            request = urllib.request.Request(
                f"{self.url}/predict",
                data=payload,
                headers={"content-type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                data = json.loads(response.read())
            point = data.get("point_norm")
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ValueError(f"no point_norm in response: {str(data)[:120]}")
            x = float(point[0]) / 1000.0
            y = float(point[1]) / 1000.0
            if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                raise ValueError(f"point_norm outside 0..1000: {point!r}")
        except Exception:  # noqa: BLE001 - the detector chain is the fallback by design
            self._failures += 1
            if self._failures >= 2:
                self._skip_until = time.monotonic() + self.cooldown_s
            return None
        self._failures = 0
        return x, y

    def detect(self, image: bytes, near: tuple[float, float] | None = None) -> Pointer | None:
        point = self.locate(image, self.prompt)
        if point is None:
            return None
        return Pointer(point[0], point[1], 0.9, "locate-anything")
