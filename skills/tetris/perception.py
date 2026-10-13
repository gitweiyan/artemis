"""Reading the board off the screen — deterministic computer vision, no model.

The pipeline, in order, each step a function that can be tested on a recorded frame:

1. **proportional crop** — ``roi.board_crop`` takes the playfield as a *ratio* of the
   screen and normalises it to 400x800, so every later step works on one geometry;
2. **grid, measured not assumed** — ``grid_probe.measure_grid`` reads the cell pitch
   from the periodic structure of the crop and checks it against the crop's own extent.
   No periodicity means "not a board": a menu, an ad, a dialog. The runtime counts that
   as a blind frame, which is how the loop stops instead of flailing at an ad;
3. **cell classification** — ``cells.read_matrix``: the centre 5x5 HSV median of each
   cell, thresholded, giving the board as rows of "0"/"1";
4. **piece separation** — ``pieces.split_pieces`` over a few recent matrices: what moved
   is the piece, what stayed is the terrain, and the displacement is the motion vector;
5. **HUD** — lines/score read from a calibrated ROI by digit-template matching. Kept
   for the caller's verdict, never for the policy.

A skill calibrated *before* the ratios existed keeps the older path (``find_grid`` on
the calibrated absolute grid, ``read_cells`` against colour prototypes); it is reached
only when the calibration has no ``board_roi_ratios``. Everything is driven by
``calibration.json``: geometry, ratios, thresholds, ROIs. Nothing in this file
hardcodes a particular game.

The state returned carries both worlds: the measured ``board_matrix`` /
``current_piece_mask`` / ``motion_vector`` that :mod:`policy_jev` consumes, and a
letter-named board for the older letter-based search policy, whose name is a *match*
against the standard seven (- ``None`` for a custom shape, which has no letter).
"""

from __future__ import annotations

from collections import deque
from typing import Any

from . import panel, stall
from .cells import read_matrix
from .grid_probe import declared_cells, identity_problem, measure_grid
from .roi import RATIO_KEY, board_crop, roi_pixels
from .simulator import PIECE_NAMES, mask_cells, orientations
from .tracker import PieceTracker

EMPTY = "."

#: Cell colours as calibrated (RGB), and which cell letter each maps to.
DEFAULT_TOLERANCE = 60.0

#: How many recent matrices the motion step keeps. Two frames carry the motion
#: (`pieces.split_pieces`); the rest are context for a reader of a recorded session.
MAX_HISTORY = 4

_previous: list[list[str]] | None = None
_settled: list[list[str]] | None = None
_history: deque[list[str]] = deque(maxlen=MAX_HISTORY)
#: How still the playfield has been (``stall.py``). Built on the first parsed frame, from
#: the calibration, and reset with the rest of the history.
_stall: Any = None
#: The falling piece tracked across frames (``tracker.py``). Also cross-frame state, and
#: reset with the rest of the history: a new round must not inherit the last piece.
_tracker: PieceTracker | None = None


def _piece_tracker(calibration: dict[str, Any]) -> PieceTracker:
    global _tracker

    if _tracker is None:
        _tracker = PieceTracker(calibration)
    return _tracker


def _stall_tracker(calibration: dict[str, Any]):
    global _stall

    if _stall is None:
        _stall = stall.StallTracker(**stall.settings(calibration))
    return _stall


def reset_history(*_args, **_kwargs) -> None:
    """Forget the recent frames — a new session must not inherit the last one's motion.

    Also clears what the older path remembered (the last board and its settled stack) and
    the stillness history, for the same reason: a new round starts with a fresh board, and
    inheriting the last one's "nothing has moved for five seconds" would end it instantly.
    Called by the manifest's ``on_start`` hook (:mod:`session`), and takes any arguments
    because the runtime hands a hook its skill handle.
    """
    global _previous, _settled

    _history.clear()
    _previous = None
    _settled = None
    if _stall is not None:
        _stall.reset()
    if _tracker is not None:
        _tracker.reset()


def _decode(frame: Any):
    """JPEG/PNG bytes → an ``(H, W, 3)`` float array, or None when undecodable.

    Accepts a capture object (``.image``) *and* the raw bytes themselves: ``panel`` takes
    either, and a caller that hands one function bytes and the other a capture would
    otherwise get "no board" from a frame that is perfectly readable — a silent,
    plausible-looking wrong answer.
    """
    try:
        import io

        import numpy as np
        from PIL import Image
    except ImportError:  # pragma: no cover - numpy/PIL are hard deps of the project
        return None
    raw = getattr(frame, "image", None)
    if raw is None and isinstance(frame, (bytes, bytearray, memoryview)):
        raw = bytes(frame)
    if not raw:
        return None
    try:
        image = Image.open(io.BytesIO(bytes(raw))).convert("RGB")
    except (OSError, ValueError, TypeError):
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


_calibration_cache: dict[str, Any] | None = None


def calibration() -> dict[str, Any]:
    """This skill's own calibration, read once.

    The runtime calls ``parse(frame)`` — it has no business knowing where a skill
    keeps its numbers — so the skill loads its own file. Without this the grid and
    the colour prototypes arrive empty and every frame is blind, which is exactly
    what happened on the first live run (15/15 blind frames, 2026-10-10).
    """
    global _calibration_cache
    if _calibration_cache is None:
        import json
        from pathlib import Path as _Path

        path = _Path(__file__).with_name("calibration.json")
        try:
            _calibration_cache = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _calibration_cache = {}
    return _calibration_cache


def parse(frame, calibration: dict[str, Any] | None = None):
    """``perception.parse(frame) -> state | None`` — the reflex runtime's contract.

    ``None`` means "not a board": the runtime counts it as a blind frame and stops the
    session past its threshold, which is the right answer for a menu, an ad or a dialog.

    The game-over panel is checked *before* the board, and returns a state rather than
    ``None``: the identity check below rejects the panel (it is not a 10x20 board either),
    so without this the session would end on blind frames with no reason recorded, and the
    caller would not know it should restart rather than stop.
    """
    calibration = calibration or globals()["calibration"]()
    image = _decode(frame)
    if image is None:
        return None
    # The stillness reading is taken for every frame, before any of the checks below: a
    # frame this pipeline rejects as "not a board" is exactly the kind of frame that also
    # stops moving, and the outer loop wants that measurement either way.
    reading = _stall_tracker(calibration).push(stall.now(), stall.board_roi(image, calibration))
    if panel.is_game_over(image, calibration):
        return _game_over_state(image, calibration, reading)
    if RATIO_KEY in calibration:
        return _measured_state(image, calibration, reading)
    return _legacy_state(image, calibration, reading)


def _game_over_state(image, calibration: dict[str, Any], reading=None) -> dict[str, Any]:
    """The state for a screen showing the game-over panel: no board, and a reason to stop.

    The board keys are present but empty so that a reader (or a policy) that asks for them
    gets "nothing" rather than a ``KeyError`` — the panel covers the playfield, so there is
    no honest board to report.
    """
    counters = read_hud(image, calibration)
    return {
        "board_matrix": None,
        "board": [],
        "current_piece_mask": [],
        "current_piece_positions": [],
        "motion_vector": None,
        "current": None,
        "grid": None,
        "next": None,
        "falling_cells": [],
        "lines": counters.get("lines"),
        "score": counters.get("score"),
        "counters": counters,
        "game_over": True,
        "stall": reading,
    }


def _measured_state(image, calibration: dict[str, Any], reading=None):
    """The measured pipeline: proportional crop → grid → cells → piece separation."""
    field = board_crop(image, calibration)
    probe = measure_grid(field, calibration)
    columns, rows = probe["columns"], probe["rows"]
    if identity_problem(probe, declared_cells(calibration)) is not None:
        # Not this board: a menu, an ad, a dialog, or a screen whose texture merely looks
        # periodic (Review Focus 5). The runtime counts this as a blind frame, which is
        # how the loop stops instead of flailing at it.
        return None
    if _history and (len(_history[-1]), len(_history[-1][0])) != (rows, columns):
        # The grid changed between frames: mixing them would read motion across two
        # different geometries (and the tracker's terrain belongs to the old one).
        _history.clear()
        if _tracker is not None:
            _tracker.reset()

    measured = {
        **calibration,
        "grid": {**(calibration.get("grid") or {}), "cols": columns, "rows": rows},
    }
    matrix = read_matrix(field, measured)
    _history.append(matrix)
    split = _piece_tracker(calibration).push(matrix)
    x0, y0, x1, y1 = roi_pixels(image.shape, calibration)
    counters = read_hud(image, calibration)
    return {
        "board_matrix": split["board_matrix"],
        # The same board for the letter-based search policy: it only distinguishes
        # filled from empty, and its `current` carries a name when there is one.
        "board": [[EMPTY if cell == "0" else "#" for cell in row] for row in split["board_matrix"]],
        "current_piece_mask": split["current_piece_mask"],
        "current_piece_positions": split["current_piece_positions"],
        # How many frames this piece's identity has been tracked. 1 means it was just
        # acquired - and the game draws a piece over two frames, so a decision on frame 1
        # is a decision about half a piece (measured: 48% of those land where planned,
        # against 88% for decisions on a settled shape; `capture` note in calibration.json).
        "piece_frames": split.get("piece_frames"),
        # The piece's bitmap unchanged from the previous frame. The policy waits for it before
        # planning: a plan for a half-revealed shape carries shapes the revealed piece never
        # matches (device 2026-10-13 run14: a 2-cell domino planned r1c5, then a 3-cell bar,
        # and the rotate guard ended the session).
        "piece_settled": split.get("piece_settled"),
        # The tracker's lifecycle id. The policy uses it to keep "one rotation, one shift,
        # one drop" per *piece*, not per decision: a reveal changes the mask, which changes
        # the decision key, but it is still the same piece.
        "piece_id": split.get("piece_id"),
        "motion_vector": split["motion_vector"],
        "current": _name_of(split["current_piece_mask"]),
        "grid": {
            "origin_x": x0,
            "origin_y": y0,
            # Floats: this board's cell is 42.75 px tall, and the control layer needs that
            # precision to aim a swipe at the right row.
            "cell_w": (x1 - x0) / columns,
            "cell_h": (y1 - y0) / rows,
            "cols": columns,
            "rows": rows,
        },
        "grid_probe": probe,
        "next": None,
        "falling_cells": [(column, row) for row, column in split["current_piece_positions"]],
        "lines": counters.get("lines"),
        "score": counters.get("score"),
        "counters": counters,
        # `parse` already answered the panel question for this frame (it checks first), so
        # a board that got this far is not the panel.
        "game_over": False,
        # How still the playfield has been: the inner loop ends the session on it (see
        # `stall.py`), and the outer loop decides whether that means the round is over.
        "stall": reading,
    }


def _name_of(mask) -> str | None:
    """The standard name of a shape, when it has one.

    A convenience for the letter-based search policy, never for Jev: a game with custom
    shapes has no letters, and ``None`` is the honest answer there (Review Focus 4).
    """
    if not mask:
        return None
    shape = mask_cells(mask)
    for letter in PIECE_NAMES:
        if shape in orientations(letter):
            return letter
    return None


def _legacy_state(image, calibration: dict[str, Any], reading=None):
    """The older path: a calibrated absolute grid and colour prototypes."""
    global _previous, _settled

    grid = find_grid(image, calibration)
    if grid is None:
        return None
    board = read_cells(image, grid, calibration)

    if _previous is not None and board != _previous and calibration.get("require_stable", False):
        # Opt-in only. It was on by default and made *every* frame blind on a live
        # game: a falling piece changes the board between captures by definition, so
        # "two consecutive frames must agree" rejects the very thing we are reading.
        # What must be stable is the settled stack, and that is handled below.
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
        # As in `_measured_state`: the panel question is `parse`'s, answered before this.
        "game_over": False,
        "stall": reading,
    }


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


__all__ = ["find_grid", "parse", "read_cells", "read_hud", "reset_history"]
