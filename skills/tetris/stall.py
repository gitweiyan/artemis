"""A playfield that stops changing: how the inner loop notices the round is over.

The round ends silently as far as the app is concerned — no accessibility node, no text
this loop can read (it is a Unity surface, and the repo's OCR needs a cloud key). What the
loop *can* measure is that the picture stops moving. That is this module, and it is
deliberately only a measurement: "the playfield has looked the same for five seconds" is a
fact, while "therefore the game is over" is a conclusion the outer loop draws (a paused
game, a dialog and a frozen app all look the same from here).

Two details make the difference between this working and misfiring constantly, and both
were measured rather than chosen (``calibration.json`` → ``stall.measured``):

* **brightness is divided out** before comparing. A modal scrim dimmed the whole screen to
  0.61x on the device; un-normalised, that reads as "every pixel changed", so a perfectly
  static board would never look still. Dividing each frame by its own mean removes any
  uniform dimming or brightening;
* **the threshold is on the size of the change**, and it is far above the usual 95 %. Over
  30 s of a game *being played*, five-second windows of the playfield are 93.0 %–98.2 %
  similar — a piece moving one row changes ~2 % of a 359 100-pixel field. The game-over
  panel scores 0.999. The threshold sits between those, at **0.995**.

The playfield is compared downsampled 4x (16x less memory per retained frame, same verdict:
0.9823 against 0.9823 on the worst window of the recorded session).
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any, Sequence

from .roi import RATIO_KEY, roi_pixels

#: How long the playfield must hold still before it counts as stopped.
DEFAULT_WINDOW_S = 5.0
#: What fraction of the downsample blocks must agree. See the measurement above.
DEFAULT_MIN_SIMILARITY = 0.995
#: A block counts as changed once it differs by this much of the frame's mean brightness.
DEFAULT_TOLERANCE = 0.12
#: Block size for the comparison: 4x4 pixels.
DEFAULT_DOWNSAMPLE = 4
#: Frames kept. At ~0.7 s per frame this is a few windows' worth; a long session must not
#: grow without bound.
DEFAULT_MAX_SAMPLES = 64


def settings(calibration: dict[str, Any] | None = None) -> dict[str, Any]:
    """The tracker's parameters, from ``calibration.json`` → ``stall`` (with defaults).

    A skill calibrated before this block existed still gets usable numbers rather than an
    exception inside the loop.
    """
    block = (calibration or {}).get("stall") or {}
    return {
        "window_s": float(block.get("window_s") or DEFAULT_WINDOW_S),
        "min_similarity": float(block.get("min_similarity") or DEFAULT_MIN_SIMILARITY),
        "tolerance": float(block.get("tolerance") or DEFAULT_TOLERANCE),
        "downsample": int(block.get("downsample") or DEFAULT_DOWNSAMPLE),
        "max_samples": int(block.get("max_samples") or DEFAULT_MAX_SAMPLES),
    }


def now() -> float:
    """The clock the tracker is fed with. A function so tests can drive time."""
    return time.monotonic()


def board_roi(image, calibration: dict[str, Any] | None = None, *, downsample: int | None = None):
    """The playfield as a brightness-normalised, downsampled grayscale array (or ``None``).

    ``None`` means "there is nothing to compare": no ``board_roi_ratios`` in the
    calibration (an older skill), an empty crop, or a frame too dark to have a mean. The
    caller must treat that as "no reading", never as "unchanged".
    """
    import numpy as np

    calibration = calibration or {}
    if RATIO_KEY not in calibration:
        return None
    x0, y0, x1, y1 = roi_pixels(image.shape, calibration)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    grey = np.asarray(image[y0:y1, x0:x1], dtype="float32").mean(axis=2)
    mean = float(grey.mean())
    if mean <= 0:
        return None
    grey = grey / mean
    factor = int(downsample or settings(calibration)["downsample"])
    if factor < 2:
        return grey
    height = (grey.shape[0] // factor) * factor
    width = (grey.shape[1] // factor) * factor
    if height < factor or width < factor:
        return grey
    return (
        grey[:height, :width]
        .reshape(height // factor, factor, width // factor, factor)
        .mean(axis=(1, 3))
    )


def similarity(a, b, tolerance: float = DEFAULT_TOLERANCE) -> float:
    """The fraction of blocks that agree within ``tolerance``.

    Both sides must already be normalised by their own mean (see :func:`board_roi`), which
    is what makes a uniform dimming or brightening of the screen irrelevant.
    """
    import numpy as np

    if a is None or b is None or a.shape != b.shape:
        return 0.0
    return float((np.abs(a - b) <= tolerance).mean())


class StallTracker:
    """Keeps the recent playfields and reports how long the newest one has held still.

    ``push(t, roi)`` is the whole interface: it returns a reading, and the reading's
    ``stalled`` flag is simply "the playfield has not changed for ``window_s``".
    """

    def __init__(
        self,
        *,
        window_s: float = DEFAULT_WINDOW_S,
        min_similarity: float = DEFAULT_MIN_SIMILARITY,
        tolerance: float = DEFAULT_TOLERANCE,
        downsample: int = DEFAULT_DOWNSAMPLE,
        max_samples: int = DEFAULT_MAX_SAMPLES,
    ):
        self.window_s = float(window_s)
        self.min_similarity = float(min_similarity)
        self.tolerance = float(tolerance)
        self.downsample = int(downsample)
        self.max_samples = max(2, int(max_samples))
        self._samples: deque[tuple[float, Any]] = deque(maxlen=self.max_samples)

    # -- the interface ------------------------------------------------------ #

    def push(self, when: float, roi) -> dict[str, Any]:
        """Add one playfield reading and report the current stillness."""
        if roi is None:
            # No comparable picture (no ratios, a black frame): the history is broken —
            # continuing to compare against frames from before it would be a guess.
            self._samples.clear()
            return self.reading(ready=False)
        self._samples.append((float(when), roi))
        return self.reading()

    def reset(self) -> None:
        """Forget everything — a new session must not inherit the last one's stillness."""
        self._samples.clear()

    def resident_samples(self) -> int:
        """How many frames are held in memory (bounded; see ``max_samples``)."""
        return len(self._samples)

    def reading(self, *, ready: bool | None = None) -> dict[str, Any]:
        """The current stillness: similarity, how long it has held, and the verdict."""
        if len(self._samples) < 2:
            return self._blank(ready=False)
        when, newest = self._samples[-1]
        oldest_similar = when
        for older_when, older in reversed(list(self._samples)[:-1]):
            if similarity(newest, older, self.tolerance) < self.min_similarity:
                break
            oldest_similar = older_when
        static_s = when - oldest_similar
        window_similarity: float | None = None
        span = 0.0
        has_window = False
        for older_when, older in reversed(self._samples):
            if when - older_when >= self.window_s:
                window_similarity = similarity(newest, older, self.tolerance)
                span = when - older_when
                has_window = True
                break
        reading = {
            "similarity": window_similarity,
            "span_s": round(span, 3),
            "static_s": round(static_s, 3),
            "window_s": self.window_s,
            "min_similarity": self.min_similarity,
            "samples": len(self._samples),
            "ready": has_window if ready is None else bool(ready),
            "stalled": static_s >= self.window_s,
        }
        return reading

    def _blank(self, *, ready: bool) -> dict[str, Any]:
        return {
            "similarity": None,
            "span_s": 0.0,
            "static_s": 0.0,
            "window_s": self.window_s,
            "min_similarity": self.min_similarity,
            "samples": len(self._samples),
            "ready": ready,
            "stalled": False,
        }


def stalled(state: Any) -> dict[str, Any] | None:
    """The reading when this state says the playfield has stopped, else ``None``.

    One helper for both policies, so the rule is written once and cannot drift between the
    search backend and the Jev one.
    """
    if not isinstance(state, dict):
        return None
    reading = state.get("stall")
    if isinstance(reading, dict) and reading.get("stalled"):
        return reading
    return None


def note(reading: dict[str, Any]) -> str:
    """What to tell the session log: the measurement, and no conclusion beyond it."""
    return f"board static for {reading.get('static_s')}s (similarity {reading.get('similarity')})"


__all__ = [
    "DEFAULT_MAX_SAMPLES",
    "DEFAULT_MIN_SIMILARITY",
    "DEFAULT_TOLERANCE",
    "DEFAULT_WINDOW_S",
    "StallTracker",
    "board_roi",
    "note",
    "now",
    "settings",
    "similarity",
    "stalled",
]
