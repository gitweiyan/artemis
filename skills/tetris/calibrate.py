"""Calibration: turn one real screenshot into the numbers the skill needs.

A vision-based skill is only as good as its calibration, and calibration needs a
human — or, as here, *text*: this module renders what it sees as an ASCII board, a
colour palette and a per-cell HSV table, so a reader (or an agent without image input)
can judge whether perception reads the screen correctly before any gameplay is
attempted.

What it derives, all from pixels and printed for inspection:

* the board grid, **measured** — the cell pitch comes from the periodic structure of the
  playfield crop and is checked against that crop's own extent (``grid_probe``), so
  "10 columns" is a reading rather than an assumption;
* the classified board as text, so a wrong threshold or a shifted grid is *visible*;
* every cell's own HSV next to the thresholds — a cell that reads empty because it
  *just* missed them is the cell that will flip when the light changes, and it is
  invisible in a matrix of 0s and 1s;
* whether the frame is a dark screen rather than a game (the device sleeps).

Nothing here writes the calibration file: it prints a proposal for a human to accept.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from third_party.mobile_use.utils.logger import get_logger

from .cells import cell_hsv, read_matrix
from .grid_probe import declared_cells, identity_problem, measure_grid
from .perception import calibration as skill_calibration
from .pieces import split_pieces
from .roi import board_crop

logger = get_logger(__name__)

SAMPLES_DIR = "samples"

#: Below this mean brightness the frame is a dark screen, not a calibration: the device
#: sleeps, and a black frame would otherwise "verify" as an empty board.
SLEEP_BRIGHTNESS = 10.0
#: How short of a threshold a cell has to be for the review to call it borderline.
BORDERLINE_MARGIN = 15.0


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


# --------------------------------------------------------------------------- #
# Verification: the numbers a reader has to be able to check
# --------------------------------------------------------------------------- #


def verify_report(
    image: bytes,
    calibration: dict[str, Any] | None = None,
    *,
    history: list[list[str]] | None = None,
) -> dict[str, Any]:
    """One frame → the grid measurement, the board, each cell's HSV, and a sleep check.

    ``history`` is optional: motion needs two frames, so a single image reports
    ``motion_vector: None`` rather than inventing one. Pass the matrices of the frames
    that came before this one and the motion is measured, exactly as the loop measures
    it (``pieces.split_pieces``).
    """
    calibration = calibration or skill_calibration()
    array = _decode(image)
    field = board_crop(array, calibration)
    probe = measure_grid(field, calibration)
    columns, rows = probe["columns"], probe["rows"]
    matrix: list[str] = []
    hsv: list[list[tuple[int, int, int]]] = []
    if columns and rows:
        measured = {
            **calibration,
            "grid": {**(calibration.get("grid") or {}), "cols": columns, "rows": rows},
        }
        matrix = read_matrix(field, measured)
        hsv = cell_hsv(field, measured)
    motion = split_pieces([*history, matrix])["motion_vector"] if history and matrix else None
    return {
        "grid": {"columns": columns, "rows": rows, "extent_ok": bool(probe["extent_ok"])},
        "matrix": matrix,
        "cell_hsv": hsv,
        "motion_vector": motion,
        "suspected_sleep_screen": bool(float(array.mean()) < SLEEP_BRIGHTNESS),
    }


def board_problem(report: dict[str, Any], calibration: dict[str, Any] | None = None) -> str | None:
    """Why this frame is not this app's board, or ``None`` when it is.

    The same judgement the perception loop makes before it acts on a frame, so a
    calibration run cannot pass a screen the loop would refuse.
    """
    calibration = calibration or skill_calibration()
    return identity_problem(report.get("grid") or {}, declared_cells(calibration))


def render_verification(report: dict[str, Any], calibration: dict[str, Any] | None = None) -> str:
    """The report as text: what was measured, and what could be measured wrong."""
    calibration = calibration or skill_calibration()
    thresholds = calibration.get("hsv") or {}
    v_min = float(thresholds.get("v_min") or 0.0)
    s_min = float(thresholds.get("s_min") or 0.0)
    grid = report["grid"]
    out: list[str] = []
    if report["suspected_sleep_screen"]:
        out.append(
            f"screen: DARK (mean brightness < {SLEEP_BRIGHTNESS:g}) — the device was probably"
            " asleep; wake it and capture again"
        )
    problem = board_problem(report, calibration)
    if not grid["columns"] or not grid["rows"]:
        out.append(f"grid: NOT MEASURED — {problem}")
        out.append("extent: nothing to check without a grid")
        return "\n".join(out)

    out.append(
        f"grid: {grid['columns']} x {grid['rows']} cells — extent"
        f" {'closed' if grid['extent_ok'] else 'NOT closed (the pitch does not divide the field)'}"
    )
    if problem:
        out.append(
            f"identity: NOT THIS BOARD — {problem}: check that the game is on screen, and"
            " that board_roi_ratios and grid still describe this app"
        )
    out.append(
        "motion: "
        + (
            str(report["motion_vector"])
            if report["motion_vector"] is not None
            else "not measured (one frame: motion needs two)"
        )
    )
    out.append("board (0 empty, 1 block):")
    out.append(render(report["matrix"]))
    out.append(
        f"cells near the threshold (v_min={v_min:g}, s_min={s_min:g}) — these are the"
        " ones that flip when the light does:"
    )
    borderline = _borderline(report, v_min, s_min)
    out.extend(borderline or ["   none: every empty cell is clearly empty"])
    return "\n".join(out)


def _borderline(report: dict[str, Any], v_min: float, s_min: float) -> list[str]:
    """Empty cells whose *largest* shortfall from a threshold is small.

    A cell that fails on both counts — dark *and* grey, like UI chrome — is not
    borderline: it is clearly empty, and no threshold tweak is going to change that.
    """
    lines: list[str] = []
    for row, (line, hsv_row) in enumerate(zip(report["matrix"], report["cell_hsv"], strict=True)):
        for column, (cell, (h, s, v)) in enumerate(zip(line, hsv_row, strict=True)):
            if cell != "0":
                continue
            shortfall = max(v_min - v, s_min - s)
            if 0 < shortfall <= BORDERLINE_MARGIN:
                lines.append(
                    f"   row {row}, column {column}: H{h} S{s} V{v}"
                    f" (short by {shortfall:.0f} of the {BORDERLINE_MARGIN:.0f} that would matter)"
                )
    return lines


__all__ = [
    "BORDERLINE_MARGIN",
    "SAMPLES_DIR",
    "board_problem",
    "SLEEP_BRIGHTNESS",
    "analyse",
    "grid_from_blocks",
    "json_report",
    "palette",
    "proposal",
    "read_board",
    "render",
    "render_report",
    "render_verification",
    "save_frame",
    "settle_and_capture",
    "verify_report",
]
