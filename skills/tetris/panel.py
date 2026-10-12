"""The game-over panel and the restart button — both read off the screen, both offline.

The session has to end when the app says so, and start again when the operator's loop
asks for another round. This game gives neither signal in the accessibility tree (it is a
Unity surface), and the repo's OCR is a cloud service, which a 2-3 fps inner loop must not
call. So both come from the pixels:

* **the panel** — a small committed patch of the title (``panel.template``) is matched
  against the same rectangle of every frame. The rectangle is a *ratio* of the screen, so
  another resolution moves the box with it; the patch itself is a crop of a real
  screenshot, taken where the operator read "游戏结束" on the live screen. Nothing here
  interprets the glyphs — the match is a pixel correlation, and the identity of the text
  rests on the operator's reading (see ``calibration.json``'s ``panel.measured``);
* **the button** — the tap point is the operator's reading of the same screen, and the
  label under it is checked against the frame's own white glyphs
  (``tests/unit/reflex/test_panel.py``), so a typo in the ratio cannot silently tap the
  background.

The correlation is zero-mean normalised (the same function the numbers in the calibration
were measured with, so a measured score is comparable to a live one). A flat patch — a
black screen, a solid colour — has no variance to correlate and scores 0 rather than
dividing by zero: "no evidence" must not read as "perfect match".

``restart`` takes the screen reader and the tapper as arguments instead of owning a device,
which is what lets the plan's open question — *does this button need one tap or two?* — be
settled by looking at the screen after each tap rather than guessed in a calibration file.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

#: The calibration block describing the panel patch.
PANEL_KEY = "panel"
#: The block describing where the restart button is and how long to wait after a tap.
RESTART_KEY = "restart_flow"
#: A correlation this high in practice means "the same pixels"; the calibrated threshold
#: is far below it, because the measured gap is 1.000 against 0.051 (see the calibration).
DEFAULT_THRESHOLD = 0.6
DEFAULT_MAX_TAPS = 3
DEFAULT_WAIT_S = 5.0
#: Below this standard deviation a patch has no structure to correlate.
FLAT = 1e-6

_templates: dict[str, Any] = {}


def _numpy():
    import numpy as np

    return np


def template(calibration: dict[str, Any] | None = None):
    """The title patch as a 2-D float array, or ``None`` when this skill has none.

    Loaded from the skill directory (the calibration names the file, never an absolute
    path) and cached: the inner loop calls this every frame, and re-reading a PNG per
    frame is work that buys nothing.
    """
    block = (calibration or {}).get(PANEL_KEY) or {}
    name = block.get("template")
    if not name:
        return None
    try:
        path = Path(__file__).parent / str(name)
    except (TypeError, ValueError):  # a calibration typo must not raise inside the loop
        return None
    key = str(path)
    if key not in _templates:
        np = _numpy()
        try:
            from PIL import Image

            # RGB, then the same mean-of-channels the live patch uses. Loading the file as
            # "L" instead would apply PIL's luma weights on one side and a plain mean on the
            # other: the same pixels then correlate at 0.999, not 1.000, and every recorded
            # score would be off by a little forever.
            with Image.open(path) as image:
                rgb = np.asarray(image.convert("RGB"), dtype="float32")
            _templates[key] = rgb.mean(axis=2)
        except (OSError, ValueError):
            _templates[key] = None
    return _templates[key]


def box(shape, ratios) -> tuple[int, int, int, int]:
    """``(x0, y0, x1, y1)`` pixels for ratios of an image of ``shape``.

    ``(x0, y0, x1, y1)`` is the order ``roi.RATIO_KEY`` uses; clamped to the image, so a
    ratio outside 0-1 cannot wrap around to the opposite edge and match something else.
    """
    height, width = int(shape[0]), int(shape[1])
    x0, y0, x1, y1 = (float(value) for value in ratios[:4])
    return (
        min(max(round(x0 * width), 0), width),
        min(max(round(y0 * height), 0), height),
        min(max(round(x1 * width), 0), width),
        min(max(round(y1 * height), 0), height),
    )


def image_of(frame):
    """A frame as an ``(H, W, 3)`` float array: raw bytes, a capture, or an array."""
    np = _numpy()
    if isinstance(frame, np.ndarray):
        return frame
    data = getattr(frame, "image", frame)
    if not data:
        return None
    try:
        import io

        from PIL import Image

        return np.asarray(Image.open(io.BytesIO(bytes(data))).convert("RGB"), dtype="float32")
    except (OSError, ValueError, TypeError):
        return None


def _grey(image, roi_ratios):
    x0, y0, x1, y1 = box(image.shape, roi_ratios)
    patch = image[y0:y1, x0:x1]
    if patch.size == 0:
        return None
    return patch.mean(axis=2)


def _resized(patch, shape) -> Any:
    """The template at the patch's size — a no-op on the resolution it was measured on."""
    if patch.shape == tuple(shape):
        return patch
    from PIL import Image

    np = _numpy()
    return np.asarray(
        Image.fromarray(patch.astype("uint8")).resize((shape[1], shape[0]), Image.LANCZOS),
        dtype="float32",
    )


def correlation(patch, patch_template) -> float:
    """Zero-mean normalised cross-correlation of two same-sized grayscale patches."""
    np = _numpy()
    if patch is None or patch_template is None or patch.shape != patch_template.shape:
        return 0.0
    centred_patch = patch - patch.mean()
    centred_template = patch_template - patch_template.mean()
    denominator = float(np.sqrt((centred_patch**2).sum() * (centred_template**2).sum()))
    if denominator < FLAT:
        return 0.0
    return float((centred_patch * centred_template).sum() / denominator)


def score(frame, calibration: dict[str, Any] | None = None) -> float:
    """How much this frame's title box looks like the panel patch, in ``[-1, 1]``.

    ``0`` when the skill has no patch, when the frame cannot be read, or when the patch is
    flat — never a number that would pass a threshold by accident.
    """
    calibration = calibration or {}
    patch = template(calibration)
    ratios = ((calibration.get(PANEL_KEY) or {}).get("roi_ratios")) or None
    image = image_of(frame)
    if patch is None or image is None or not ratios:
        return 0.0
    grey = _grey(image, ratios)
    if grey is None:
        return 0.0
    return correlation(grey, _resized(patch, grey.shape))


def is_game_over(frame, calibration: dict[str, Any] | None = None) -> bool:
    """Whether the game-over panel is on screen — the session's own end signal.

    The threshold comes from the calibration; the measured gap between the panel and the
    game being played is wide enough (1.000 vs 0.051) that any value in between works, and
    the tests pin the margin rather than the exact number.
    """
    block = (calibration or {}).get(PANEL_KEY) or {}
    threshold = float(block.get("match_threshold") or DEFAULT_THRESHOLD)
    return score(frame, calibration) >= threshold


def restart_point(calibration: dict[str, Any] | None = None) -> tuple[int, int] | None:
    """Where the restart button is, in pixels, or ``None`` when it is not calibrated.

    A ratio of the calibrated screen, like the other buttons: another resolution is a
    calibration edit, not a code change.
    """
    block = (calibration or {}).get(RESTART_KEY) or {}
    ratios = block.get("button_ratios")
    screen = (calibration or {}).get("screen") or {}
    if not ratios or len(ratios) != 2 or not screen.get("width") or not screen.get("height"):
        return None
    return (
        round(float(ratios[0]) * float(screen["width"])),
        round(float(ratios[1]) * float(screen["height"])),
    )


def restart(
    look: Callable[[], Any],
    tap: Callable[[tuple[int, int]], Any],
    calibration: dict[str, Any] | None = None,
    *,
    max_taps: int | None = None,
    wait: float | None = None,
    sleep: Callable[[float], Any] = time.sleep,
) -> dict[str, Any]:
    """Tap the restart button until the panel is gone, then report what it took.

    ``look()`` returns a fresh frame (anything :func:`image_of` accepts) and ``tap(point)``
    presses it — the device stays outside this function, so the loop is testable without
    one. Taps stop as soon as the panel is off the screen: a blind second tap on a screen
    that already restarted would land in the middle of the new board.

    Returns ``{"taps": int, "restarted": bool, "reason": str}``. ``restarted`` is about the
    panel, not about the board being playable: whether the new board reads correctly is
    the caller's check (``perception.parse`` returning a state), and conflating the two
    would hide a broken board behind a successful restart.
    """
    calibration = calibration or {}
    point = restart_point(calibration)
    if point is None:
        return {"taps": 0, "restarted": False, "reason": "no restart point in the calibration"}
    if not is_game_over(look(), calibration):
        return {"taps": 0, "restarted": True, "reason": "no panel on screen"}

    block = calibration.get(RESTART_KEY) or {}
    if wait is None:
        wait = float(block.get("wait_s") or DEFAULT_WAIT_S)
    if max_taps is None:
        max_taps = int(block.get("max_taps") or DEFAULT_MAX_TAPS)
    taps = 0
    while taps < max_taps:
        tap(point)
        taps += 1
        sleep(wait)
        if not is_game_over(look(), calibration):
            return {"taps": taps, "restarted": True, "reason": "the panel is gone"}
    return {
        "taps": taps,
        "restarted": False,
        "reason": f"the panel is still on screen after {taps} taps",
    }


__all__ = [
    "PANEL_KEY",
    "RESTART_KEY",
    "box",
    "correlation",
    "image_of",
    "is_game_over",
    "restart",
    "restart_point",
    "score",
    "template",
]
