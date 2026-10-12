"""One cell, one bit: is there a block in it? Measured over the cell, not sampled.

A cell is filled when **enough of its pixels** are a block's pixels:

* **an occupancy fraction, not a centre sample** — the device's blocks are 39 px on a
  42.5 px pitch, and the game animates a falling piece *between* rows: a piece a third
  of the way down covers ~40% of each of the two cells it spans, and a 5x5 centre sample
  misses both of them. Measured on the device frames, the two judges agree on settled
  blocks and only the occupancy judge sees the moving one;
* **a fraction, not an average** — block edges are anti-aliased and glowing, and the
  border of a cell is where the gridline is; averaging over the whole cell dilutes a
  block that does not fill it, while counting the pixels that pass says how much of the
  cell is block;
* **HSV with two thresholds** — the game's field is a deep blue theme whose *empty*
  background is saturated (S ~ 70 of 100), so saturation alone calls every empty cell
  a block; brightness alone calls white UI text (V ~ 94, S ~ 0) a block. A pixel counts
  only when it is both bright and saturated.

Thresholds (``hsv.v_min``/``s_min``, ``hsv.min_occupancy``, the cell margin) come from
``calibration.json``; nothing here hardcodes a game's theme or its board size.
"""

from __future__ import annotations

from typing import Any

#: Side of the centre square used by :func:`cell_hsv` for the calibration review.
CENTRE_PIXELS = 5
#: How much of a cell's inner region to ignore, so the gridline and a block's own glow at
#: the cell's border do not decide the reading. Measured: device blocks start ~3 px inside
#: a 42.5 px cell, so 0.12 of a normalised cell keeps them and drops the line.
INNER_MARGIN = 0.12

EMPTY = "0"
FILLED = "1"


def _geometry(field, calibration: dict[str, Any]) -> tuple[int, int, float, float]:
    """``(rows, cols, cell_height, cell_width)`` from the calibration and the field."""
    grid = (calibration or {}).get("grid") or {}
    rows, cols = grid.get("rows"), grid.get("cols")
    if not rows or not cols:
        raise ValueError("calibration is missing grid.rows/grid.cols: the cell grid is not known")
    height, width = int(field.shape[0]), int(field.shape[1])
    return int(rows), int(cols), height / int(rows), width / int(cols)


def _thresholds(calibration: dict[str, Any]) -> tuple[float, float, float]:
    """``(v_min, s_min, min_occupancy)`` — all three from the calibration file."""
    hsv = (calibration or {}).get("hsv") or {}
    if "v_min" not in hsv or "s_min" not in hsv or "min_occupancy" not in hsv:
        raise ValueError(
            "calibration is missing hsv.v_min/hsv.s_min/hsv.min_occupancy:"
            " no cell verdict is possible"
        )
    return float(hsv["v_min"]), float(hsv["s_min"]), float(hsv["min_occupancy"])


def _field_hsv(field):
    """The whole field as HSV (H 0-360, S and V 0-100), per pixel."""
    import numpy as np

    array = np.asarray(field, dtype="float32")[:, :, :3]
    return rgb_to_hsv(array.reshape(-1, 3)).reshape(array.shape[0], array.shape[1], 3)


def _sampled_hsv(field, calibration: dict[str, Any]):
    """``(rows, cols, 3)`` median HSV of every cell's centre square.

    Saturation and value are 0-100, hue 0-360 — the scale the calibration thresholds
    are written in.
    """
    import numpy as np

    rows, cols, cell_h, cell_w = _geometry(field, calibration)
    array = np.asarray(field, dtype="float32")[:, :, :3]
    height, width = array.shape[:2]
    half = CENTRE_PIXELS // 2
    offsets = np.arange(CENTRE_PIXELS) - half
    centres_y = np.round((np.arange(rows) + 0.5) * cell_h).astype(int)
    centres_x = np.round((np.arange(cols) + 0.5) * cell_w).astype(int)
    ys = np.clip(centres_y[:, None] + offsets[None, :], 0, max(0, height - 1))
    xs = np.clip(centres_x[:, None] + offsets[None, :], 0, max(0, width - 1))
    patches = array[ys[:, None, :, None], xs[None, :, None, :]]  # (rows, cols, 5, 5, 3)
    pixels = patches.reshape(-1, 3)
    hsv = rgb_to_hsv(pixels).reshape(rows, cols, CENTRE_PIXELS * CENTRE_PIXELS, 3)
    return np.median(hsv, axis=2)


def rgb_to_hsv(rgb):
    """``(N, 3)`` RGB in 0-255 → ``(N, 3)`` HSV with H in 0-360, S and V in 0-100."""
    import numpy as np

    values = np.asarray(rgb, dtype="float32").reshape(-1, 3)
    maximum = values.max(axis=1)
    minimum = values.min(axis=1)
    delta = maximum - minimum
    saturation = np.where(maximum > 0, delta / np.maximum(maximum, 1e-6), 0.0) * 100.0
    hue = _hue(values, maximum, delta)
    return np.stack([hue, saturation, maximum / 255.0 * 100.0], axis=1)


def _hue(values, maximum, delta):
    import numpy as np

    red, green, blue = values[:, 0], values[:, 1], values[:, 2]
    safe = np.maximum(delta, 1e-6)
    hue = np.select(
        [delta <= 0, maximum == red, maximum == green],
        [0.0, (green - blue) / safe, 2.0 + (blue - red) / safe],
        default=4.0 + (red - green) / safe,
    )
    return (hue * 60.0) % 360.0


def cell_hsv(field, calibration: dict[str, Any]) -> list[list[tuple[int, int, int]]]:
    """Every cell's ``(hue, saturation, value)``, for a human reviewing a calibration.

    The verdict alone ("0"/"1") cannot show *why* a cell went the way it did; a
    reader comparing these numbers with the thresholds can.
    """
    hsv = _sampled_hsv(field, calibration)
    return [[(int(round(h)), int(round(s)), int(round(v))) for h, s, v in row] for row in hsv]


def cell_occupancy(field, calibration: dict[str, Any]) -> list[list[float]]:
    """Every cell's fraction of block-coloured pixels — the number the verdict comes from."""
    import numpy as np

    rows, cols, cell_h, cell_w = _geometry(field, calibration)
    v_min, s_min, _cut = _thresholds(calibration)
    hsv = _field_hsv(field)
    hit = (hsv[:, :, 2] > v_min) & (hsv[:, :, 1] > s_min)
    out: list[list[float]] = []
    for r in range(rows):
        line: list[float] = []
        for c in range(cols):
            y0 = int(r * cell_h + INNER_MARGIN * cell_h)
            y1 = int((r + 1) * cell_h - INNER_MARGIN * cell_h)
            x0 = int(c * cell_w + INNER_MARGIN * cell_w)
            x1 = int((c + 1) * cell_w - INNER_MARGIN * cell_w)
            patch = hit[y0:y1, x0:x1]
            line.append(float(patch.mean()) if patch.size else 0.0)
        out.append(line)
    return out


def read_matrix(field, calibration: dict[str, Any]) -> list[str]:
    """The board as ``rows`` strings of ``"0"``/``"1"``, one character per column.

    ``"1"`` means "a block is here". Which colour the block is stays the game's
    business, not this module's: the policy needs the shape of the stack, and the
    falling piece is separated by motion (``pieces.py``), not by hue.
    """
    _v_min, _s_min, cut = _thresholds(calibration)
    return [
        "".join(FILLED if fraction >= cut else EMPTY for fraction in line)
        for line in cell_occupancy(field, calibration)
    ]


__all__ = [
    "CENTRE_PIXELS",
    "EMPTY",
    "FILLED",
    "INNER_MARGIN",
    "cell_hsv",
    "cell_occupancy",
    "read_matrix",
    "rgb_to_hsv",
]
