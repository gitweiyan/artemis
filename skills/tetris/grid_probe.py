"""How many cells the board has — measured, never assumed.

This module exists because of a specific failure. On 2026-10-12 the playfield was
called 10x20, "corrected" to 8x16 with a described counting method, then corrected
back to 10x20: three positions in three turns, and nobody had measured anything.
The evidence behind all three was a 1:2 aspect ratio, which 8x16 and 10x20 *share* —
an observation with zero power to tell them apart.

So nothing here is told the answer, and nothing here assumes "a Tetris board is 10
wide". The method:

1. **edge energy** — ``abs(diff(gray))`` along each axis: the cell boundaries of a
   playfield show up as a periodic signal, whether they are gridlines or the edges of
   blocks sitting on them;
2. **the period of that signal** — from its autocorrelation, not from the spacing of
   detected peaks. Measured on the device's own frames (see below): peak-start spacing
   gives a median gap of 38 px because the glow around a boundary is asymmetric and
   splits peaks, while the autocorrelation peaks at 40 px with 0.70;
3. **the count, checked against the field's own extent** — ``n * pitch == extent``.
   This is the part with real discriminating power: a pitch that does not divide the
   field is not this field's pitch.

The periodicity threshold rejects a field with no structure at all, and it is *not*
enough on its own to tell a board from a menu: measured, the three real board frames of
this game sit at 0.554-0.561, while 47 of the 149 unique real frames in this repo's
``traces/`` — video-app screens, the Android settings list — reach up to 0.802 at 6-8 px
pitches and close their extent. The ranges overlap, so identity is a separate check
(:func:`identity_problem`): the count has to agree with the calibration. A field with no
periodic structure reports ``None`` rather than an invented grid.
"""

from __future__ import annotations

from typing import Any

#: The autocorrelation must reach this before any period is reported at all.
MIN_PERIODICITY = 0.35
#: Periods whose autocorrelation is this close to the best are all candidates: the
#: measurement alone cannot separate them, so the field's extent breaks the tie.
AMBIGUITY = 0.05
#: Plausible cell pitches, in pixels, for a normalised field.
MIN_PITCH = 6
MAX_PITCH = 200
#: A board is at least this many cells across; one cell with borders is a frame.
MIN_CELLS = 2
#: The extent tolerance from the plan: ``n * pitch`` must land this close to the field.
EXTENT_TOLERANCE = 2


def edge_profile(gray, along: str = "x"):
    """Edge energy per pixel along ``along`` — the periodic signal the grid lives in."""
    import numpy as np

    axis = 1 if along == "x" else 0
    return np.abs(np.diff(gray, axis=axis)).mean(axis=1 - axis)


def period_candidates(profile) -> tuple[int, ...]:
    """Integer periods this profile's own autocorrelation cannot rule out, smallest first.

    Smallest first is deliberate: for a signal that is periodic at ``p`` it is also
    periodic at ``2p``, and the fundamental — the cell, not a pair of cells — is the
    smallest lag the correlation cannot distinguish from the best one.
    """
    import numpy as np

    values = np.asarray(profile, dtype="float32").ravel()
    if values.size < 3 * MIN_PITCH:
        return ()
    centred = values - values.mean()
    if not np.any(centred):
        return ()
    correlation = np.correlate(centred, centred, mode="full")[centred.size - 1 :]
    correlation = correlation / correlation[0]
    upper = min(MAX_PITCH, correlation.size - 1)
    if upper <= MIN_PITCH:
        return ()
    best = float(correlation[MIN_PITCH : upper + 1].max())
    if best < MIN_PERIODICITY:
        return ()
    return tuple(
        lag for lag in range(MIN_PITCH, upper + 1) if float(correlation[lag]) >= best - AMBIGUITY
    )


def pitch(profile) -> int | None:
    """The dominant period of an edge-energy profile in pixels, or ``None``."""
    candidates = period_candidates(profile)
    return candidates[0] if candidates else None


def measure_grid(field) -> dict[str, Any]:
    """``pitch_x/pitch_y/columns/rows/extent_ok`` for a normalised playfield crop.

    ``columns``/``rows`` stay ``None`` when the field has no periodic structure —
    that is the "not a board" answer, and callers treat it as a blind frame.
    ``extent_ok`` is False when a grid was found but does not divide the field: a
    grid that does not fit its own extent is not trustworthy, even though it is
    worth reporting so a calibration reader can see *how* it failed.
    """
    import numpy as np

    array = np.asarray(field, dtype="float32")
    gray = array.mean(axis=2) if array.ndim == 3 else array
    pitch_x, columns, ok_x = _measure_axis(gray, "x", int(gray.shape[1]))
    pitch_y, rows, ok_y = _measure_axis(gray, "y", int(gray.shape[0]))
    return {
        "pitch_x": pitch_x,
        "pitch_y": pitch_y,
        "columns": columns,
        "rows": rows,
        "extent_ok": bool(pitch_x is not None and pitch_y is not None and ok_x and ok_y),
    }


def _measure_axis(gray, along: str, extent: int) -> tuple[int | None, int | None, bool]:
    """``(pitch, cells, extent_ok)`` along one axis, with the extent as the tie-break."""
    candidates = period_candidates(edge_profile(gray, along))
    if not candidates:
        return None, None, False
    best: tuple[int, int, int] | None = None
    for candidate in candidates:
        cells = int(round(extent / candidate))
        if cells < MIN_CELLS:
            continue
        residual = abs(cells * candidate - extent)
        if best is None or residual < best[2]:
            best = (candidate, cells, residual)
    if best is None:
        return None, None, False
    return best[0], best[1], best[2] <= EXTENT_TOLERANCE


def declared_cells(calibration: dict[str, Any] | None) -> tuple[int, int] | None:
    """The cell count the calibration declares for this app, or ``None`` if it declares one.

    One reading of ``grid.cols``/``grid.rows`` for every caller that has to compare a
    measurement with an expectation — the loop and a calibration run must not disagree
    about what "the wrong board" means.
    """
    grid = (calibration or {}).get("grid") or {}
    if grid.get("cols") and grid.get("rows"):
        return int(grid["cols"]), int(grid["rows"])
    return None


def identity_problem(probe: dict[str, Any], expected: tuple[int, int] | None = None) -> str | None:
    """Why this measurement is **not** the board you were looking for, or ``None``.

    The measurement above is deliberately assumption-free, which makes it *inclusive*:
    measured over the 149 unique real 720x1600 frames in this repository's ``traces/``,
    47 of them — a video-app screen, the Android settings list — pass it with a closed
    extent, at 6-8 px pitches. Their periodicity is 0.395-0.802 against 0.554-0.561 for
    the three real boards of this game, so those ranges overlap and no threshold on that
    number separates a board from a menu.

    Identity therefore has to be a *check* rather than an inference: the measured count
    has to agree with what the calibration declares for this app. A caller that acts on a
    frame (the perception loop) treats a problem as a blind frame; a caller that reports
    (a calibration run) prints it and fails the run.
    """
    columns, rows = probe.get("columns"), probe.get("rows")
    if not columns or not rows:
        return "no grid measured: the field has no periodic structure"
    if not probe.get("extent_ok"):
        return f"the measured pitch does not divide the field ({columns} x {rows} of it)"
    if expected and (columns, rows) != (int(expected[0]), int(expected[1])):
        return (
            f"measured {columns} x {rows} cells, but the calibration declares"
            f" {int(expected[0])} x {int(expected[1])}"
        )
    return None


__all__ = [
    "AMBIGUITY",
    "EXTENT_TOLERANCE",
    "MAX_PITCH",
    "MIN_CELLS",
    "MIN_PERIODICITY",
    "MIN_PITCH",
    "declared_cells",
    "edge_profile",
    "identity_problem",
    "measure_grid",
    "period_candidates",
    "pitch",
]
