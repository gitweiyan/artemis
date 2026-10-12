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
DEFAULT_CYCLES = 1
#: Title-box brightness (0-255) below which a scrim is over the screen: 53.6 dimmed vs
#: 88.1 undimmed, measured on the device, so the threshold sits in that gap.
DEFAULT_DIMMED_BELOW = 70.0
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


def dimmed(frame, calibration: dict[str, Any] | None = None) -> bool | None:
    """Whether a scrim is over the screen — a dialog or an ad is open on top of it.

    The panel's own check cannot answer this: a zero-mean normalised correlation matches the
    panel just as well behind a 0.61x scrim, which is exactly how "the panel is still there"
    hid an open dialog on the first device run (a second attempt then closed the dialog
    instead of pressing its button). The measured discriminator is the title box's own
    brightness: 88.1 with the panel alone, 53.6 once a scrim is over it.

    Only meaningful *while the panel is on screen*: the title box is the panel's own text,
    so on a playing screen it is showing the board and its brightness says nothing about a
    scrim (measured: 61.2 with a game under way).

    ``None`` when there is nothing to measure (no frame, no box).
    """
    calibration = calibration or {}
    block = calibration.get(PANEL_KEY) or {}
    ratios = block.get("roi_ratios")
    image = image_of(frame)
    if image is None or not ratios:
        return None
    x0, y0, x1, y1 = box(image.shape, ratios)
    band = image[y0:y1, x0:x1]
    if band.size == 0:
        return None
    threshold = float(block.get("dimmed_below") or DEFAULT_DIMMED_BELOW)
    return float(band.mean()) < threshold


def restart_points(calibration: dict[str, Any] | None = None) -> tuple[tuple[int, int], ...]:
    """The buttons to press, in order, to start a new round — in pixels.

    This game needs **two**: the panel's own 重新开始 opens a confirm dialog and dims the
    screen, and the dialog's left button is what actually starts the round (the operator
    walked through both on the device). A single ``button_ratios`` — an older calibration —
    is still accepted as a one-step flow.

    Ratios, not pixels: another resolution is a calibration edit, not a code change.
    """
    calibration = calibration or {}
    block = calibration.get(RESTART_KEY) or {}
    screen = calibration.get("screen") or {}
    width, height = screen.get("width"), screen.get("height")
    if not width or not height:
        return ()
    steps = block.get("steps")
    if not steps:
        single = block.get("button_ratios")
        steps = [{"at_ratio": single}] if single else []
    points: list[tuple[int, int]] = []
    for step in steps:
        ratios = (step or {}).get("at_ratio")
        if isinstance(ratios, (list, tuple)) and len(ratios) == 2:
            points.append(
                (round(float(ratios[0]) * float(width)), round(float(ratios[1]) * float(height)))
            )
    return tuple(points)


def restart_point(
    calibration: dict[str, Any] | None = None, index: int = 0
) -> tuple[int, int] | None:
    """One of those buttons (the first by default), or ``None`` when uncalibrated."""
    points = restart_points(calibration)
    return points[index] if 0 <= index < len(points) else None


def _brightness(frame) -> float | None:
    """The frame's mean brightness — how the dimming between the two steps shows up."""
    image = image_of(frame)
    return round(float(image.mean()), 1) if image is not None else None


def restart(
    look: Callable[[], Any],
    tap: Callable[[tuple[int, int]], Any],
    calibration: dict[str, Any] | None = None,
    *,
    start: int | None = None,
    cycles: int | None = None,
    wait: float | None = None,
    sleep: Callable[[float], Any] = time.sleep,
) -> dict[str, Any]:
    """Press the restart buttons in order until the panel is gone, then report what it took.

    ``look()`` returns a fresh frame (anything :func:`image_of` accepts) and ``tap(point)``
    presses it — the device stays outside this function, so the flow is testable without
    one.

    One pass over the calibrated steps, looking at the screen after each: if the panel goes
    away after the first button, the second is never pressed — it belongs to a dialog that
    is no longer there, and the tap would land on whatever is underneath (on this game, the
    middle of a fresh board). ``cycles`` repeats the whole sequence for an app that drops
    the dialog once.

    The between-steps check is the *panel's*, deliberately: a normalised correlation still
    matches the panel behind the dialog, so "the panel is gone" means the round really
    started rather than "the screen got darker".

    An already-dimmed screen means a dialog (or an ad) is open *now*: pressing the panel's
    own button would only close it, so the flow starts at the step after it and says so in
    the report. ``start`` overrides that for a caller that knows better — the outer loop can
    read the screen, this function can only measure its brightness.

    Returns ``{"taps", "restarted", "reason", "steps", "started_at_step"}``; each step
    records the point it pressed and what the screen looked like right after. ``restarted`` is about the panel,
    not about the board being playable — whether the new board reads correctly is the
    caller's check (``perception.parse`` returning a state), and conflating the two would
    hide a broken board behind a successful restart.
    """
    calibration = calibration or {}
    points = restart_points(calibration)
    if not points:
        return {"taps": 0, "restarted": False, "reason": "no restart point in the calibration"}
    first = look()
    if not is_game_over(first, calibration):
        return {"taps": 0, "restarted": True, "reason": "no panel on screen", "steps": []}

    block = calibration.get(RESTART_KEY) or {}
    if wait is None:
        wait = float(block.get("wait_s") or DEFAULT_WAIT_S)
    if cycles is None:
        cycles = int(block.get("cycles") or DEFAULT_CYCLES)

    if start is None:
        start = 1 if (len(points) > 1 and dimmed(first, calibration)) else 0
    start = max(0, min(int(start), len(points) - 1))
    taps = 0
    steps: list[dict[str, Any]] = []
    for _ in range(max(1, cycles)):
        for point in points[start:]:
            tap(point)
            taps += 1
            sleep(wait)
            frame = look()
            steps.append(
                {
                    "point": list(point),
                    "score": round(score(frame, calibration), 4),
                    "brightness": _brightness(frame),
                }
            )
            if not is_game_over(frame, calibration):
                return {
                    "taps": taps,
                    "restarted": True,
                    "reason": "the panel is gone",
                    "steps": steps,
                    "started_at_step": start,
                }
    return {
        "taps": taps,
        "restarted": False,
        "reason": f"the panel is still on screen after {taps} taps",
        "steps": steps,
        "started_at_step": start,
    }


__all__ = [
    "PANEL_KEY",
    "RESTART_KEY",
    "box",
    "correlation",
    "dimmed",
    "image_of",
    "is_game_over",
    "restart",
    "restart_point",
    "restart_points",
    "score",
    "template",
]
