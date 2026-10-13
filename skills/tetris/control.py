"""Turning a placement into taps and swipes — the last skill-specific step.

A placement (rotation + target column) is not a gesture; how a given app accepts
"rotate" and "move to column 7" is exactly what calibration is for. Everything here
reads from ``calibration.json``: the board geometry the perception step produced,
and a ``gestures`` block naming the input dialect:

    "screen":   {"width": 720, "height": 1600},
    "gestures": {
      "rotate":     {"kind": "tap", "at_ratio": [0.81, 0.84]},
      "move_left":  {"kind": "tap", "at_ratio": [0.13, 0.84]},
      "move_right": {"kind": "tap", "at_ratio": [0.53, 0.84]},
      "drop":       {"kind": "tap", "at_ratio": [0.33, 0.84], "taps": 1}
    }

Buttons, not swipes: this game draws its own four controls along the bottom, so a move is
``|delta|`` taps on the left or right button and the fall is a tap on the drop button. The
positions are **fractions of the screen**, multiplied by ``screen`` here, so another
resolution is a calibration edit rather than a code change.
"""

from __future__ import annotations

from artemis.reflex.contract import Gesture


def _tap_point(entry: dict, calibration: dict) -> tuple[float, float] | None:
    """Where a button is: either absolute pixels, or a fraction of the screen."""
    screen = (calibration or {}).get("screen") or {}
    ratio = entry.get("at_ratio")
    if ratio and len(ratio) == 2 and screen.get("width") and screen.get("height"):
        return (
            float(ratio[0]) * float(screen["width"]),
            float(ratio[1]) * float(screen["height"]),
        )
    at = entry.get("at")
    if at and len(at) == 2:
        return (float(at[0]), float(at[1]))
    return None


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
    phase = (action.state or {}).get("phase")

    # The turn and the placement are executed on separate frames, so that the placement is
    # measured on a frame that shows the piece already turned - the device turns a piece
    # about its second cell, so the column changes as it turns.
    taps = (action.state or {}).get("rotate_taps")
    if taps is None:
        # `move` and `hold` both mean the turn is behind us. `hold` was missing from this
        # list, so every hold frame tapped rotate again by defaulting to `placement.rotation`
        # — measured on the device with the click log: the policy said hold and the gesture
        # layer kept sending rotations, frame after frame. The search policy sets no phase at
        # all and relies on this default, so it stays for "no phase given".
        taps = 0 if phase in ("move", "hold") else int(placement.get("rotation") or 0)
    rotate_point = _tap_point(dialect.get("rotate") or {}, cal or {})
    for _ in range(int(taps)):
        if rotate_point is None:
            break
        gestures.append(Gesture.tap(*rotate_point, description="rotate"))

    if phase == "rotate":
        return gestures

    delta = (action.state or {}).get("move_columns")
    if delta is None:
        target_column = int(placement.get("column") or 0)
        delta = target_column - int(current_column) if current_column is not None else 0
    if delta:
        # One tap moves the piece one column: the buttons are the game's own controls,
        # so a four-column move is four taps on the same button.
        delta = int(delta)
        button = "move_right" if delta > 0 else "move_left"
        point = _tap_point(dialect.get(button) or {}, cal or {})
        for _ in range(abs(delta)):
            if point is None:
                break
            gestures.append(Gesture.tap(*point, description=button.replace("_", " ")))

    # The drop button is driven by calibration. It is enabled for this game since 2026-10-13
    # (the operator cancelled the no-hard-drop rule): dropping on the same frame as the shift
    # locks the piece where the plan put it, instead of letting it fall several more rows while
    # no further decision is made. It goes out after the shift, never on a turn frame —
    # turning is only useful if the piece is still falling on the next frame.
    drop = dialect.get("drop") or {}
    point = _tap_point(drop, cal or {})
    if drop.get("enabled") and point is not None:
        for _ in range(int(drop.get("taps") or 1)):
            gestures.append(Gesture.tap(*point, description="drop"))
    return gestures


__all__ = ["to_gestures"]
