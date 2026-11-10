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
import threading
import time
import urllib.request

from artemis.reflex.vision import Pointer
from third_party.mobile_use.utils.logger import get_logger

logger = get_logger(__name__)

__all__ = [
    "LocateAnythingDetector",
    "DEFAULT_LOCATE_URL",
    "DEFAULT_LOCATE_PROMPT",
    "warm_up_locate_anything",
]

DEFAULT_LOCATE_URL = "http://127.0.0.1:7861"
DEFAULT_LOCATE_PROMPT = "the blue circular mouse pointer"

#: The frame a warm-up sends. Any image works: the cost being paid is the model load.
_WARMUP_PROMPT = "the blue circular mouse pointer"
#: URLs already warmed in this process (a warm-up is per service, not per client).
_WARMED: set[str] = set()


def _probe_png() -> bytes:
    """A small, valid PNG: the service reads the size from the bytes."""
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (64, 64), (32, 32, 32)).save(buffer, "PNG")
    return buffer.getvalue()


def warm_up_locate_anything(url: str, *, timeout_s: float = 60.0) -> threading.Thread | None:
    """Load the service's model in the background, once per URL per process.

    The first inference after an idle period loads the model (measured 3.6 s and 25.5 s
    on this rig). Paying that inside a click's 10 s budget means the click times out,
    the detector backs off, and the run clicks on unverified coordinates — the
    2026-11-06 15:58 failure. Returns the thread (a test seam) or None when the URL was
    already warmed.
    """
    key = str(url or "").strip().rstrip("/")
    if not key or key in _WARMED:
        return None
    _WARMED.add(key)

    def run() -> None:
        LocateAnythingDetector(key, timeout_s=timeout_s).warm_up()

    thread = threading.Thread(target=run, name="locate-anything-warmup", daemon=True)
    thread.start()
    return thread


def _data_url(image: bytes) -> str:
    mime = "image/png"
    if image[:3] == b"\xff\xd8\xff":
        mime = "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(image).decode('ascii')}"


class LocateAnythingDetector:
    """A ``detect(image, near)`` detector backed by the LocateAnything service."""

    source_id = "locate-anything"

    def __init__(
        self,
        url: str = DEFAULT_LOCATE_URL,
        *,
        prompt: str = DEFAULT_LOCATE_PROMPT,
        timeout_s: float = 4.0,
        cooldown_s: float = 4.0,
        max_cooldown_s: float = 60.0,
    ):
        self.url = str(url).rstrip("/")
        self.prompt = str(prompt)
        self.timeout_s = max(0.1, float(timeout_s))
        #: The skip after the second consecutive failure, doubled for later ones up to
        #: ``max_cooldown_s``. A fixed 60 s skip covered the rest of a short run
        #: (2026-11-06 15:58) and silently disabled target resolution.
        self.cooldown_s = max(0.0, float(cooldown_s))
        self.max_cooldown_s = max(self.cooldown_s, float(max_cooldown_s))
        self._failures = 0
        self._skip_until = 0.0
        #: Why the last call returned None (``timeout``, ``connection refused``, ...).
        self.last_error: str | None = None
        self.last_elapsed_ms: float = 0.0

    def warm_up(self) -> bool:
        """Pay the model load now, in this thread; never raises and never counts as a failure.

        Uses its own (long) budget so a cold load is not mistaken for a broken service.
        The caller normally runs this in a daemon thread at run start.
        """
        started = time.monotonic()
        try:
            payload = json.dumps(
                {
                    "image": _data_url(_probe_png()),
                    "prompt": _WARMUP_PROMPT,
                    "w": 0,
                    "h": 0,
                }
            ).encode("utf-8")
            request = urllib.request.Request(
                f"{self.url}/predict",
                data=payload,
                headers={"content-type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=max(30.0, self.timeout_s)):
                pass
        # Network/encoding failures are the whole failure surface here. Anything else is a
        # bug: this runs in a daemon thread, so it dies without touching the run.
        except (OSError, ValueError, TypeError) as exc:
            logger.info(
                f"LocateAnything warm-up failed at {self.url} "
                f"({(time.monotonic() - started) * 1000:.0f} ms): {exc}"
            )
            return False
        logger.info(
            f"LocateAnything warm-up primed {self.url} in "
            f"{(time.monotonic() - started) * 1000:.0f} ms"
        )
        return True

    def locate(self, image: bytes, prompt: str) -> tuple[float, float] | None:
        """The service's 0..1 point for ``prompt``, or None while unavailable."""
        # Two consecutive failures mean the service is not usable right now; skipping it
        # keeps every observation from paying two timeouts in a row.
        if time.monotonic() < self._skip_until:
            self.last_error = "cooling down after repeated failures"
            return None
        started = time.monotonic()
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
        except Exception as exc:  # noqa: BLE001 - the detector chain is the fallback by design
            self._failures += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.last_elapsed_ms = (time.monotonic() - started) * 1000.0
            if self._failures >= 2:
                delay = min(self.max_cooldown_s, self.cooldown_s * 2 ** (self._failures - 2))
                self._skip_until = time.monotonic() + delay
            logger.warning(
                f"LocateAnything {self.url} {prompt!r} failed in "
                f"{self.last_elapsed_ms:.0f} ms: {self.last_error}"
            )
            return None
        self._failures = 0
        self.last_error = None
        self.last_elapsed_ms = (time.monotonic() - started) * 1000.0
        logger.debug(
            f"LocateAnything {self.url} {prompt!r} -> [{round(x * 1000)}, {round(y * 1000)}] "
            f"in {self.last_elapsed_ms:.0f} ms"
        )
        return x, y

    def detect(self, image: bytes, near: tuple[float, float] | None = None) -> Pointer | None:
        point = self.locate(image, self.prompt)
        if point is None:
            return None
        return Pointer(point[0], point[1], 0.9, "locate-anything")
