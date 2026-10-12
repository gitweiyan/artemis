"""Turning a placement into taps and swipes — the last skill-specific step.

A placement (rotation + target column) is not a gesture; how a given app accepts
"rotate" and "move to column 7" is exactly what calibration is for. Everything here
reads from ``calibration.json``: the board geometry the perception step produced,
and a ``gestures`` block naming the input dialect:

    "gestures": {
      "rotate":   {"kind": "tap", "at": [360, 1180]},
      "hard_drop":{"kind": "swipe", "from": [360, 300], "to": [360, 1100], "duration_ms": 140},
      "move":     {"kind": "swipe", "y": 900, "duration_ms": 90},
      "settle_frames": 2
    }

With ``move`` the skill swipes horizontally inside the board: one swipe that covers
the whole column delta is faster than N single-cell swipes, and the app's own
repeat timer does the rest.
"""

from __future__ import annotations

from artemis.reflex.contract import Gesture


def _board_geometry(state, calibration):
    """Board origin and cell size: perception's grid, or the calibrated one.

    Floats, not ints: this board's cell is 42.75 px tall, and truncating it puts a
    piece's resting row 15 px off by the bottom of the board — the swipe would aim at a
    row the piece is not in.
    """
    grid = (state or {}).get("grid") or (calibration or {}).get("grid") or {}
    origin_x = float(grid.get("origin_x") or 0)
    origin_y = float(grid.get("origin_y") or 0)
    cell_w = float(grid.get("cell_w") or 0)
    cell_h = float(grid.get("cell_h") or 0)
    return origin_x, origin_y, cell_w, cell_h


def to_gestures(action, calibration=None, *, state=None):
    """``control.to_gestures(action)`` — the reflex runtime's contract.

    The runtime passes the action only; the calibration is attached to the action's
    state by the policy, so a skill can be exercised offline without a context.
    """
    cal = (
        calibration if calibration is not None else ((action.state or {}).get("calibration") or {})
    )
    dialect = (cal or {}).get("gestures") or {}
    placement = (action.state or {}).get("placement") or {}
    current_column = (action.state or {}).get("current_column")
    if not dialect or not placement:
        return []

    gestures: list[Gesture] = []
    origin_x, origin_y, cell_w, cell_h = _board_geometry(state or (action.state or {}), cal)

    for _ in range(int(placement.get("rotation") or 0)):
        rotate = dialect.get("rotate") or {}
        x, y = rotate.get("at") or (0, 0)
        gestures.append(Gesture.tap(x, y, description="rotate"))

    target_column = int(placement.get("column") or 0)
    if current_column is not None and cell_w:
        delta = target_column - int(current_column)
        if delta:
            move = dialect.get("move") or {}
            y = int(move.get("y") or (origin_y + cell_h))
            per_cell = int(move.get("per_cell_px") or cell_w)
            start_x = origin_x + int(current_column) * cell_w + cell_w / 2
            end_x = start_x + delta * per_cell
            gestures.append(
                Gesture.swipe(
                    start_x,
                    y,
                    end_x,
                    y,
                    duration_ms=int(move.get("duration_ms") or 90),
                    description=f"move {delta:+d}",
                )
            )

    drop = dialect.get("hard_drop") or {}
    if drop:
        start = drop.get("from") or [origin_x, origin_y]
        end = drop.get("to") or [origin_x, origin_y + cell_h * 10]
        gestures.append(
            Gesture.swipe(
                start[0],
                start[1],
                end[0],
                end[1],
                duration_ms=int(drop.get("duration_ms") or 140),
                description="hard drop",
            )
        )
    return gestures


__all__ = ["to_gestures"]
