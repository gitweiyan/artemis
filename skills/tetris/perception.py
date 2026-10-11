"""Reading the board off the screen — deterministic computer vision, no model.

Pipeline, in order, each step a function that can be tested on a recorded frame:

1. **grid fit** — threshold the seven saturated block colours, project the mask onto
   the rows and columns, and read the periodic peaks: that gives the cell size and
   the board origin. No periodicity means "not on the board" (menu, ad, loading),
   which the runtime counts as a blind frame.
2. **cell classification** — the median colour of each cell's inner region against
   the colour prototypes sampled during calibration (nearest neighbour in RGB).
3. **stability** — a board is only trusted when two consecutive frames agree, which
   is what makes line-clear animations harmless.
4. **falling piece** — the settled stack is the per-cell mode over recent frames;
   whatever differs from it is the piece in play (and the row it occupies).
5. **HUD** — lines/score read from a calibrated ROI by digit-template matching. Kept
   for the caller's verdict, never for the policy.

Everything is driven by ``calibration.json``: geometry, colours, ROIs. Nothing in
this file hardcodes a particular game.
"""

from __future__ import annotations

from typing import Any

EMPTY = "."

#: Cell colours as calibrated (RGB), and which cell letter each maps to.
DEFAULT_TOLERANCE = 60.0

_previous: list[list[str]] | None = None
_settled: list[list[str]] | None = None


def _decode(frame: Any):
    """JPEG/PNG bytes → an ``(H, W, 3)`` float array, or None when undecodable."""
    try:
        import io

        import numpy as np
        from PIL import Image
    except ImportError:  # pragma: no cover - numpy/PIL are hard deps of the project
        return None
    try:
        image = Image.open(io.BytesIO(bytes(getattr(frame, "image", b"")))).convert("RGB")
    except (OSError, ValueError):
        return None
    return np.asarray(image, dtype="float32")


def find_grid(image, calibration: dict[str, Any]):
    """Return ``(origin_x, origin_y, cell_w, cell_h, cols, rows)`` or None.

    A calibrated ``grid`` block short-circuits the search (the common case: the app
    does not move), otherwise the periodicity of the block mask is used.
    """
    import numpy as np

    grid = calibration.get("grid") or {}
    if all(k in grid for k in ("origin_x", "origin_y", "cell_w", "cell_h")):
        return (
            int(grid["origin_x"]),
            int(grid["origin_y"]),
            int(grid["cell_w"]),
            int(grid["cell_h"]),
            int(grid.get("cols", 10)),
            int(grid.get("rows", 20)),
        )

    colours = calibration.get("colours") or {}
    if not colours:
        return None
    mask = _block_mask(image, colours, float(calibration.get("tolerance") or DEFAULT_TOLERANCE))
    if mask.sum() < 50:
        return None
    columns = mask.sum(axis=0)
    rows = mask.sum(axis=1)
    cell_w = _period(columns)
    cell_h = _period(rows)
    if not cell_w or not cell_h:
        return None
    origin_x = int(np.argmax(columns > columns.max() * 0.25))
    origin_y = int(np.argmax(rows > rows.max() * 0.25))
    return (
        origin_x,
        origin_y,
        int(cell_w),
        int(cell_h),
        int(calibration.get("cols") or 10),
        int(calibration.get("rows") or 20),
    )


def _block_mask(image, colours: dict[str, Any], tolerance: float):
    """Pixels close to any calibrated block colour."""
    import numpy as np

    mask = np.zeros(image.shape[:2], dtype=bool)
    for rgb in colours.values():
        if not isinstance(rgb, (list, tuple)) or len(rgb) != 3:
            continue
        delta = np.abs(image - np.asarray(rgb, dtype="float32"))
        mask |= delta.max(axis=2) <= tolerance
    return mask


def _period(profile) -> int | None:
    """The dominant spacing of the peaks in a projection profile."""
    import numpy as np

    if profile.size < 8:
        return None
    threshold = profile.mean() + profile.std() * 0.25
    filled = profile > threshold
    starts = np.flatnonzero((~filled[:-1]) & filled[1:])
    if starts.size < 3:
        return None
    gaps = np.diff(starts)
    gaps = gaps[gaps > 4]
    if gaps.size == 0:
        return None
    return int(np.median(gaps))


def read_cells(image, grid, calibration: dict[str, Any]) -> list[list[str]]:
    """Classify every cell: ``'.'`` empty, otherwise the tetromino letter."""
    import numpy as np

    origin_x, origin_y, cell_w, cell_h, cols, rows = grid
    colours = calibration.get("colours") or {}
    prototypes = [
        (letter, np.asarray(rgb, dtype="float32"))
        for letter, rgb in colours.items()
        if isinstance(rgb, (list, tuple)) and len(rgb) == 3
    ]
    background = calibration.get("background")
    background_rgb = np.asarray(background, dtype="float32") if background else None
    tolerance = float(calibration.get("tolerance") or DEFAULT_TOLERANCE)
    # A game whose whole screen is saturated (deep-blue theme, glow everywhere) defeats
    # a colour-similarity classifier: every cell "matches" something. The measured
    # fallback is brightness — blocks are lit, the empty field is dark — so calibration
    # may define `brightness_threshold` instead of a palette.
    brightness = calibration.get("brightness_threshold")

    board: list[list[str]] = []
    for row in range(rows):
        line: list[str] = []
        for col in range(cols):
            y0 = origin_y + row * cell_h + max(2, cell_h // 4)
            y1 = origin_y + (row + 1) * cell_h - max(2, cell_h // 4)
            x0 = origin_x + col * cell_w + max(2, cell_w // 4)
            x1 = origin_x + (col + 1) * cell_w - max(2, cell_w // 4)
            patch = image[max(0, y0) : y1, max(0, x0) : x1]
            if patch.size == 0:
                line.append(EMPTY)
                continue
            median = np.median(patch.reshape(-1, 3), axis=0)
            if brightness is not None and not prototypes:
                line.append("#" if float(median.max()) > float(brightness) else EMPTY)
                continue
            letter = EMPTY
            if (
                background_rgb is not None
                and np.abs(median - background_rgb).max() <= tolerance * 0.5
            ):
                line.append(EMPTY)
                continue
            best = None
            for candidate, prototype in prototypes:
                distance = float(np.abs(median - prototype).max())
                if best is None or distance < best[0]:
                    best = (distance, candidate)
            if best is not None and best[0] <= tolerance:
                letter = best[1]
            line.append(letter)
        board.append(line)
    return board


def _diff(board, settled) -> list[tuple[int, int, str]]:
    if settled is None:
        return []
    out = []
    for y, row in enumerate(board):
        for x, cell in enumerate(row):
            if cell != EMPTY and (y >= len(settled) or settled[y][x] == EMPTY):
                out.append((x, y, cell))
    return out


def parse(frame, calibration: dict[str, Any] | None = None):
    """``perception.parse(frame) -> state | None`` — the reflex runtime's contract."""
    global _previous, _settled

    calibration = calibration or {}
    image = _decode(frame)
    if image is None:
        return None
    grid = find_grid(image, calibration)
    if grid is None:
        return None
    board = read_cells(image, grid, calibration)

    if _previous is not None and board != _previous and calibration.get("require_stable", True):
        # One frame of disagreement (a clear animation, a repaint): not yet trusted.
        _previous = board
        return None
    _previous = board

    falling = _diff(board, _settled)
    current = falling[0][2] if falling else None

    if _settled is None:
        _settled = [row[:] for row in board]
    else:
        for y, row in enumerate(board):
            for x, cell in enumerate(row):
                if cell != EMPTY and cell == _settled[y][x]:
                    continue
                _settled[y][x] = cell

    counters = read_hud(image, calibration)
    return {
        "board": board,
        "grid": {
            "origin_x": grid[0],
            "origin_y": grid[1],
            "cell_w": grid[2],
            "cell_h": grid[3],
            "cols": grid[4],
            "rows": grid[5],
        },
        "current": current,
        "next": None,
        "falling_cells": [(x, y) for x, y, _ in falling],
        "lines": counters.get("lines"),
        "score": counters.get("score"),
        "counters": counters,
        "game_over": bool(calibration.get("game_over_roi"))
        and _roi_matches(
            image, calibration["game_over_roi"], calibration.get("game_over_colours") or []
        ),
    }


def _roi_matches(image, roi, colours) -> bool:
    """Whether a calibrated ROI looks like a given set of colours (e.g. 'GAME OVER')."""
    import numpy as np

    y0, y1, x0, x1 = (int(v) for v in roi)
    patch = image[y0:y1, x0:x1]
    if patch.size == 0:
        return False
    median = np.median(patch.reshape(-1, 3), axis=0)
    tolerance = 80.0
    return any(
        isinstance(rgb, (list, tuple))
        and len(rgb) == 3
        and float(np.abs(median - np.asarray(rgb, dtype="float32")).max()) <= tolerance
        for rgb in colours
    )


def read_hud(image, calibration: dict[str, Any]) -> dict[str, int]:
    """Lines/score from the calibrated ROI, by digit-template matching.

    Templates live in the calibration file (``digits``: ``{"0": [[...], ...]}`` as
    small grayscale masks). Without templates the ROI is skipped and the counters
    stay empty — better than inventing numbers a verdict would then trust.
    """
    templates = calibration.get("digits") or {}
    rois = calibration.get("hud_rois") or {}
    if not templates or not rois:
        return {}
    out: dict[str, int] = {}
    for name, roi in rois.items():
        text = _read_digits(image, roi, templates, calibration)
        if text is not None:
            out[name] = int(text)
    return out


def _read_digits(image, roi, templates, calibration) -> str | None:
    """Very small OCR: segment the ROI by columns, match each glyph to a template."""
    import numpy as np

    y0, y1, x0, x1 = (int(v) for v in roi)
    patch = image[y0:y1, x0:x1]
    if patch.size == 0:
        return None
    gray = patch.mean(axis=2)
    threshold = float(calibration.get("digit_threshold") or 128.0)
    foreground = gray > threshold
    columns = foreground.sum(axis=0)
    glyphs: list[str] = []
    run_start: int | None = None
    for x, count in enumerate(columns):
        if count > 0 and run_start is None:
            run_start = x
        elif count == 0 and run_start is not None:
            glyphs.append(_match_glyph(foreground[:, run_start:x], templates, np))
            run_start = None
    if run_start is not None:
        glyphs.append(_match_glyph(foreground[:, run_start:], templates, np))
    digits = "".join(g for g in glyphs if g.isdigit())
    return digits or None


def _match_glyph(bitmap, templates, np) -> str:
    """Best matching template letter for one glyph (resized to the template box)."""
    import numpy as np_mod

    best: tuple[float, str] | None = None
    height = max(1, bitmap.shape[0])
    for letter, masks in templates.items():
        for mask in masks:
            template = np_mod.asarray(mask, dtype="float32")
            if template.ndim != 2 or template.size == 0:
                continue
            scaled = _resize_nearest(bitmap.astype("float32"), template.shape, np_mod)
            score = float(np_mod.mean(np_mod.abs(scaled - template)))
            if best is None or score < best[0]:
                best = (score, letter)
    _ = height
    return best[1] if best else ""


def _resize_nearest(array, shape, np):
    """Nearest-neighbour resize good enough for 7-segment digits."""
    target_h, target_w = shape
    source_h, source_w = array.shape
    rows = (np.arange(target_h) * source_h / target_h).astype(int)
    cols = (np.arange(target_w) * source_w / target_w).astype(int)
    rows = np.clip(rows, 0, source_h - 1)
    cols = np.clip(cols, 0, source_w - 1)
    return array[rows][:, cols]


__all__ = ["find_grid", "parse", "read_cells", "read_hud"]
