"""Calibration: turn one real screenshot into the numbers the skill needs.

A vision-based skill is only as good as its calibration, and calibration needs a
human — or, as here, *text*: this module renders what it sees as an ASCII board and
a colour palette, so a reader (or an agent without image input) can judge whether
perception reads the screen correctly before any gameplay is attempted.

What it derives, all from pixels and printed for inspection:

* the board grid (origin, cell size, rows/cols) from the periodicity of block-coloured
  pixels, or from the largest rectangular region of them;
* the colour palette, clustered, with each cluster's share;
* the classified board as text, so a wrong palette or a shifted grid is *visible*;
* how the board changes over a few seconds, which is what distinguishes "the game is
  playing" from "we are looking at a menu".

Nothing here writes the calibration file: it prints a proposal for a human to accept.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from third_party.mobile_use.utils.logger import get_logger

logger = get_logger(__name__)

SAMPLES_DIR = "samples"


def save_frame(image: bytes, *, directory: Path, tag: str) -> Path:
    """Keep one sample frame on disk (a calibration artifact, not a run artifact)."""
    directory.mkdir(parents=True, exist_ok=True)
    suffix = ".jpg" if image[:3] == b"\xff\xd8\xff" else ".png"
    path = directory / f"{tag}{suffix}"
    path.write_bytes(image)
    return path


def _decode(image: bytes):
    import io

    import numpy as np
    from PIL import Image

    return np.asarray(Image.open(io.BytesIO(image)).convert("RGB"), dtype="float32")


def palette(array, *, top: int = 12, min_saturation: float = 0.35) -> list[dict[str, Any]]:
    """The dominant *saturated* colours, quantised, with their share of pixels."""
    import numpy as np

    height, width = array.shape[:2]
    flat = array.reshape(-1, 3)
    maximum = flat.max(axis=1)
    minimum = flat.min(axis=1)
    saturation = np.where(maximum > 0, (maximum - minimum) / np.maximum(maximum, 1), 0.0)
    colourful = flat[saturation > min_saturation]
    if colourful.size == 0:
        return []
    quantised = (colourful // 24 * 24).astype("int32")
    keys, counts = np.unique(quantised, axis=0, return_counts=True)
    order = np.argsort(counts)[::-1][:top]
    total = max(1, height * width)
    return [
        {
            "rgb": [int(v) for v in keys[index]],
            "share": round(float(counts[index]) / total, 4),
            "count": int(counts[index]),
        }
        for index in order
    ]


def grid_from_blocks(array, *, min_saturation: float = 0.35, tolerance_px: int = 3):
    """Estimate ``(origin_x, origin_y, cell_w, cell_h, cols, rows)`` from a frame.

    Strategy: build the "block-ish" mask, take its bounding box (the playfield plus
    the piece preview, usually), then read the cell pitch from the strongest periodic
    peaks inside it. The pitch is taken from the *median gap between column runs*,
    which survives a partially empty board — a board with no blocks at all returns
    ``None`` rather than an invented grid.
    """
    import numpy as np

    flat = array.reshape(-1, 3)
    maximum = flat.max(axis=1)
    minimum = flat.min(axis=1)
    saturation = np.where(maximum > 0, (maximum - minimum) / np.maximum(maximum, 1), 0.0)
    mask = (saturation > min_saturation).reshape(array.shape[:2])
    if mask.sum() < 200:
        return None

    ys, xs = np.nonzero(mask)
    y0, y1, x0, x1 = int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())
    core = mask[y0 : y1 + 1, x0 : x1 + 1]

    def pitch(profile) -> int | None:
        filled = profile > profile.max() * 0.3
        starts = np.flatnonzero((~filled[:-1]) & filled[1:])
        if starts.size < 2:
            return None
        gaps = np.diff(starts)
        gaps = gaps[gaps > 4]
        return int(np.median(gaps)) if gaps.size else None

    cell_w = pitch(core.sum(axis=0))
    cell_h = pitch(core.sum(axis=1))
    if not cell_w or not cell_h:
        # Fall back to a square cell from the smaller pitch the bbox implies.
        candidates = [v for v in (cell_w, cell_h) if v]
        if not candidates:
            return None
        cell_w = cell_h = candidates[0]
    cols = max(1, int(round((x1 - x0 + 1) / cell_w)))
    rows = max(1, int(round((y1 - y0 + 1) / cell_h)))
    _ = tolerance_px
    return x0, y0, int(cell_w), int(cell_h), cols, rows


def read_board(array, grid, *, min_saturation: float = 0.25) -> list[str]:
    """Classify each cell as ``'.'`` or ``'#'`` (colour identity comes from calibration)."""
    origin_x, origin_y, cell_w, cell_h, cols, rows = grid
    lines: list[str] = []
    for row in range(rows):
        text = []
        for col in range(cols):
            y0 = origin_y + row * cell_h + max(2, cell_h // 4)
            y1 = origin_y + (row + 1) * cell_h - max(2, cell_h // 4)
            x0 = origin_x + col * cell_w + max(2, cell_w // 4)
            x1 = origin_x + (col + 1) * cell_w - max(2, cell_w // 4)
            patch = array[max(0, y0) : y1, max(0, x0) : x1]
            if patch.size == 0:
                text.append(".")
                continue
            median = patch.reshape(-1, 3).mean(axis=0)
            maximum, minimum = float(median.max()), float(median.min())
            saturation = (maximum - minimum) / maximum if maximum > 0 else 0.0
            text.append("#" if saturation > min_saturation else ".")
        lines.append("".join(text))
    return lines


def render(lines: list[str]) -> str:
    """A board with row numbers, so a reader can talk about positions."""
    width = len(str(len(lines)))
    return "\n".join(f"{index:>{width}} | {line}" for index, line in enumerate(lines, start=1))


def analyse(image: bytes, *, grid: tuple | None = None) -> dict[str, Any]:
    """One frame → the report a calibrating reader needs."""
    array = _decode(image)
    height, width = array.shape[:2]
    palette_rows = palette(array)
    found = grid or grid_from_blocks(array)
    report: dict[str, Any] = {
        "size": [width, height],
        "palette": palette_rows,
        "grid": None,
        "board": None,
        "filled_cells": 0,
    }
    if found:
        lines = read_board(array, found)
        report["grid"] = {
            "origin_x": found[0],
            "origin_y": found[1],
            "cell_w": found[2],
            "cell_h": found[3],
            "cols": found[4],
            "rows": found[5],
        }
        report["board"] = lines
        report["filled_cells"] = sum(line.count("#") for line in lines)
    return report


def render_report(report: dict[str, Any]) -> str:
    """The same report as text: what a reader needs, in the order they need it."""
    out = [f"frame: {report['size'][0]}×{report['size'][1]}"]
    out.append("palette (saturated, by share):")
    for entry in report["palette"][:8]:
        out.append(
            f"   rgb{tuple(entry['rgb'])}  {entry['share'] * 100:5.1f}%  ({entry['count']} px)"
        )
    if not report["grid"]:
        out.append("grid: NOT FOUND (no block-coloured region — menu, ad, or wrong saturation)")
        return "\n".join(out)
    grid = report["grid"]
    out.append(
        "grid: origin=({origin_x},{origin_y}) cell={cell_w}×{cell_h} {cols}×{rows}".format(**grid)
    )
    out.append(f"filled cells: {report['filled_cells']}")
    out.append("board (. empty, # block):")
    out.append(render(report["board"]))
    return "\n".join(out)


def settle_and_capture(capture, *, frames: int = 3, gap_s: float = 1.0) -> list[bytes]:
    """A few frames a second apart — enough to tell a live board from a static menu."""
    images: list[bytes] = []
    for index in range(max(1, frames)):
        frame = capture.grab()
        images.append(frame.image)
        if index + 1 < frames:
            time.sleep(max(0.0, gap_s))
    return images


def proposal(report: dict[str, Any]) -> dict[str, Any]:
    """A calibration fragment a human can paste and then correct."""
    grid = report.get("grid")
    if not grid:
        return {}
    colours = {}
    letters = ["I", "O", "T", "S", "Z", "J", "L"]
    for index, entry in enumerate(report["palette"][: len(letters)]):
        colours[letters[index]] = entry["rgb"]
    return {"grid": grid, "colours": colours}


def json_report(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2)


__all__ = [
    "SAMPLES_DIR",
    "analyse",
    "grid_from_blocks",
    "json_report",
    "palette",
    "proposal",
    "read_board",
    "render",
    "render_report",
    "save_frame",
    "settle_and_capture",
]
