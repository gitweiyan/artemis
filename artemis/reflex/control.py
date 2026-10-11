"""Gestures on the device, dispatched where they cost least.

Same two paths as capture: the helper dispatches through
``AccessibilityService.dispatchGesture`` (one loopback call, no process spawn),
``adb shell input`` is the fallback. The runtime measures what each call costs,
so the choice can be made on numbers rather than on hope.
"""

from __future__ import annotations

from typing import Any

import subprocess
import time

from artemis.reflex.attach import AttachTimeout, attach_client
from artemis.reflex.contract import Control, Gesture
from third_party.mobile_use.utils.logger import get_logger

logger = get_logger(__name__)


#: Transport failures worth turning into "no frame"/"no gesture": ``TimeoutExpired``
#: is a ``SubprocessError``, not an ``OSError`` — the same "slow is not the same as
#: absent" lesson that cost a real run 59.8 s elsewhere in this repository.
_ATTACH_ERRORS = (
    RuntimeError,
    OSError,
    subprocess.SubprocessError,
    ValueError,
    KeyError,
    AttributeError,
    ImportError,
)


class HelperControl(Control):
    """Tap/swipe through the accessibility helper."""

    def __init__(
        self, serial: str, *, client: object | None = None, attach_deadline_s: float | None = None
    ):
        self.serial = serial
        self._client: Any = client
        self._owns_client = client is None
        self.attach_deadline_s = attach_deadline_s

    def _ensure_client(self):
        if self._client is None:
            self._client = attach_client(self.serial, deadline_s=self.attach_deadline_s or 20.0)
        return self._client

    def dispatch(self, gesture: Gesture) -> float:
        try:
            client = self._ensure_client()
        except _ATTACH_ERRORS + (AttachTimeout,) as exc:
            raise RuntimeError(
                f"the accessibility helper is not usable ({exc}); enable it on the device or"
                " run with capture='screencap'"
            ) from exc
        started = time.monotonic()
        if gesture.kind == "tap":
            ((x, y),) = gesture.points
            ok = client.tap(x, y)
        elif gesture.kind == "swipe":
            (x1, y1), (x2, y2) = gesture.points
            ok = client.swipe(x1, y1, x2, y2, gesture.duration_ms)
        else:
            raise ValueError(f"unsupported gesture kind: {gesture.kind!r}")
        elapsed = (time.monotonic() - started) * 1000.0
        if not ok:
            raise RuntimeError(f"helper refused {gesture.kind} ({gesture.description})")
        return elapsed


class AdbInputControl(Control):
    """Tap/swipe through ``adb shell input`` (the always-available fallback)."""

    def __init__(self, serial: str, *, timeout_s: float = 20.0):
        self.serial = serial
        self.timeout_s = timeout_s

    def _shell(self, command: str) -> None:
        try:
            result = subprocess.run(
                ["adb", "-s", self.serial, "shell", command],
                capture_output=True,
                timeout=self.timeout_s,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            # One exception type for callers: the loop treats any dispatch failure
            # the same way, and the message says what happened.
            raise RuntimeError(f"adb input failed: {exc}") from exc
        if result.returncode != 0:
            raise RuntimeError(f"adb input failed: {result.stderr[:200]!r}")

    def dispatch(self, gesture: Gesture) -> float:
        started = time.monotonic()
        if gesture.kind == "tap":
            ((x, y),) = gesture.points
            self._shell(f"input tap {int(x)} {int(y)}")
        elif gesture.kind == "swipe":
            (x1, y1), (x2, y2) = gesture.points
            self._shell(
                f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(gesture.duration_ms)}"
            )
        else:
            raise ValueError(f"unsupported gesture kind: {gesture.kind!r}")
        return (time.monotonic() - started) * 1000.0


__all__ = ["AdbInputControl", "HelperControl"]
