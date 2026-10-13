"""The message Jev is asked about, assembled in one place.

Jev takes text and no images, so this dict is the whole interface between computer
vision and the decision: what is missing here is invisible to the model, and what is
extra here nobody agreed to read. Hence a fixed key set, built by one function.

Two of its properties are deliberate and were decided elsewhere:

* the piece travels as **geometry** — a mask plus its positions — never as a letter.
  A game with custom shapes has no letters to name them (Review Focus 4), and a letter
  would make the model's answer depend on a shape table it cannot see;
* ``mechanics.gravity_direction`` comes from the piece's **measured** displacement
  when there is one, and only falls back to the declared value otherwise. A gravity
  switch mid-session (Review Focus 3) changes the legal-move set, and an assumption
  would keep the model ranking moves that no longer exist.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

#: What a state without a declared gravity or piece source is assumed to be: the
#: standard rules and pieces read from the screen.
DEFAULT_GRAVITY = "down"
DEFAULT_PIECE_SOURCE = "vision"

#: What the skill tells the model about the task itself. Measured: without this the first
#: real call came back flat (0.34-0.37 over 18 candidates) — the message never said what
#: game it was, what the goal was, or how to read the numbers.
#:
#: The rotation is described as an *index into ``piece_orientations``*, never as
#: "clockwise": the game's rotate control has never been checked for direction on the
#: device, and a payload that leans on an unverified convention would silently invert
#: every rotation. The model gets the shapes instead.
TASK = (
    "Tetris. One piece is falling on a {columns}x{rows} board. Decide two things:"
    " how many times to turn the piece, and which column it should land in."
)
GOAL = "The best resulting stack: clear lines, keep the stack low, avoid holes and bumpiness."
EXECUTION = (
    "Before it drops, the piece is turned `rotation` times with the rotate control and slid"
    " horizontally so that its leftmost cell is in `column`; then it falls straight down"
    " until it lands."
)
#: Added to ``encoding`` only when the resulting boards are sent (the C/D arms): the model
#: has to know what the extra field is, and that it is a board, not a score.
RESULT_ENCODING = {
    "resulting_board": "the board as it would look after this candidate is played and the lines it completes are cleared; no score or evaluation is provided, compare the boards yourself",
}
ENCODING = {
    "board_matrix": "20 rows top to bottom, 10 columns left to right; '1' is a settled block, '0' is empty",
    "current_piece_mask": "the falling piece's own footprint as it looks now, rows top to bottom",
    "current_piece_positions": "where that piece is now, [row, column]",
    "piece_orientations": "the piece's shape at each rotation index; index 0 is what the screen shows now",
    "motion_vector": "[drow, dcol] movement between the last two frames; [1,0] = falling down",
    "candidate_moves": (
        "every legal placement; id = r<rotation>c<column>; rotation is an index into"
        " piece_orientations; column is where the piece's leftmost cell lands after the turn"
    ),
    "next_preview": "the next piece, or null when the screen does not show it",
}


#: Measured displacement → gravity direction.
_DIRECTIONS = {
    (1, 0): "down",
    (-1, 0): "up",
    (0, -1): "left",
    (0, 1): "right",
}


def gravity_direction(motion_vector: Sequence[int] | None, declared: str | None = None) -> str:
    """The direction the piece is actually moving in, else what was declared."""
    measured = _direction_from_motion(motion_vector)
    if measured:
        return measured
    return str(declared or DEFAULT_GRAVITY)


def _direction_from_motion(motion_vector: Sequence[int] | None) -> str | None:
    """The gravity the piece's own displacement shows, or None when it cannot know.

    Only a vertical displacement is conclusive. Sideways movement has two explanations -
    a gravity switch and the policy's own shift buttons - and they are indistinguishable
    from one frame; device 2026-10-13 showed the cost of guessing: our -4 shift was read
    as gravity "left", candidates were enumerated along the board's height, and the search
    picked column 10 on a 10-wide board. A declared direction is the honest answer there;
    only the game can say its gravity changed sideways.
    """
    if not motion_vector or len(motion_vector) != 2:
        return None
    row, _column = int(motion_vector[0]), int(motion_vector[1])
    if row == 0:
        return None
    return _DIRECTIONS[(1 if row > 0 else -1, 0)]


def build_state(
    board_matrix: Iterable[str],
    pieces: dict[str, Any] | None,
    mechanics: dict[str, Any] | None,
    next_preview,
    candidates: Iterable[Any] | None,
    *,
    resulting_boards: bool = False,
    consequences: bool = False,
) -> dict[str, Any]:
    """The state a Jev question is asked about — exactly these keys, nothing else.

    ``pieces`` is :func:`pieces.split_pieces`'s dict (a missing or empty one means the
    frame held no piece, and the message says so rather than inventing one).
    ``candidates`` are legal placements, or ``(placement, mask)`` pairs as
    :func:`simulator.legal_placements` returns them.
    """
    from .simulator import EMPTY, MASK_FILL, clear_lines, drop, orientations_from_mask

    pieces = pieces or {}
    declared = mechanics or {}
    mask = _mask(pieces.get("current_piece_mask") or [], "current_piece_mask")
    motion = _motion(pieces.get("motion_vector"))
    gravity = gravity_direction(motion, declared.get("gravity_direction"))
    board = ["".join(row) for row in board_matrix]
    return {
        "task": TASK.format(columns=len(board[0]) if board else 0, rows=len(board)),
        "goal": GOAL,
        "execution": EXECUTION,
        "encoding": dict(ENCODING),
        "board_matrix": board,
        "piece_orientations": [list(shape) for shape in orientations_from_mask(mask)]
        if mask
        else [],
        "current_piece_mask": mask,
        "current_piece_positions": [
            [int(row), int(column)] for row, column in pieces.get("current_piece_positions") or []
        ],
        "motion_vector": motion,
        "mechanics": {
            "gravity_direction": gravity,
            "piece_source": str(declared.get("piece_source") or DEFAULT_PIECE_SOURCE),
        },
        "next_preview": _preview(next_preview),
        "candidate_moves": [
            _candidate(
                entry,
                board=board,
                mask=mask,
                gravity=gravity,
                results=resulting_boards,
                consequences=consequences,
            )
            for entry in candidates or []
        ],
        **(
            {"encoding": {**ENCODING, **RESULT_ENCODING}}
            if resulting_boards
            else ({"encoding": {**ENCODING, **ACTION_ENCODING}} if consequences else {})
        ),
    }


#: The action vocabulary: the game's own four buttons plus the decision to leave the piece
#: alone. `drop` is that "leave it" option — it means "let it fall where it is" — which is why
#: there is no separate `hold`: two options with identical consequences are one option.
ACTIONS = ("left", "right", "rotate", "drop")
ACTION_DOES = {
    "left": "Slide the piece one column to the left, then let it fall.",
    "right": "Slide the piece one column to the right, then let it fall.",
    "rotate": "Turn the piece 90 degrees clockwise where it is, then let it fall.",
    "drop": "Leave the piece in this column and let it fall now.",
}
#: The reverse of each action, for the "this undoes your last move" warning.
ACTION_REVERSE = {"left": "right", "right": "left", "rotate": "rotate"}

#: What the extra fields mean, appended to ``encoding`` only when they are sent.
ACTION_ENCODING = {
    "then_lands": "what happens if you take this action and the piece then falls straight down; "
    "every number was measured from the board by the game, not estimated",
    "in_columns": "the columns the piece would occupy when it lands",
    "on_row": "the row the piece would settle on; bigger means lower, and lower is better",
    "clears_lines": "how many rows this action completes and removes",
    "new_holes": "how many new holes this action seals; prefer 0",
    "roughness_after": "how bumpy the surface is afterwards; lower is flatter",
    "plan": "the single best landing found for this piece, and the action that starts it",
    "warning": "this action undoes your previous move",
    "actions_left": "roughly how many more actions fit before the piece reaches the floor",
}

ACTION_INSTRUCTIONS = {
    "task": "Choose the single next action for the falling piece.",
    "priorities_in_order": [
        "1. CLEAR A LINE. clears_lines above 0 means this action completes rows and shortens the stack.",
        "2. LAND LOW. on_row is how deep the piece settles and BIGGER means LOWER. Never stack onto a "
        "tall column while a lower place is still reachable: that is how a mountain grows in the "
        "middle while the sides stay empty.",
        "3. MAKE NO HOLES. new_holes above 0 means sealing squares nothing can ever fill again. Prefer 0.",
        "4. STAY FLAT. Lower roughness_after means the next piece has somewhere to sit.",
        "5. LEAVE GAPS SOMETHING CAN STILL FILL. stack.dips_you_can_fill names the pieces that fit each "
        "dip; a dip only one or two pieces fit is precious, so do not cover it.",
    ],
    "where_to_aim": "stack.dips_you_can_fill lists the dips a piece can still drop into, deepest first, "
    "and stack.rows_near_completing lists the rows closest to clearing with the columns they are "
    "missing. Both are already filtered to what is reachable. Steer the piece over those columns.",
    "tie_breakers": [
        "Commit. piece.done_so_far says how far you have already moved and turned it: carry on rather "
        "than reversing. An option marked warning undoes your last action.",
        "When every option looks the same, head for an edge and pack from one side rather than leaving "
        "the piece where it happened to appear.",
        "When every option looks bad, do not thrash: pick the least bad one, fewest new_holes first, "
        "then biggest on_row, and see the piece through.",
    ],
    "important": "Every number in the options was measured from the board by the game and says what "
    "happens if you take that action and the piece falls. Judge the options on those numbers. Do not "
    "try to count squares in the grid yourself.",
}


def build_action_state(
    board_matrix: Iterable[str],
    pieces: dict[str, Any] | None,
    mechanics: dict[str, Any] | None,
    next_preview,
    *,
    rotation: int = 0,
    column: int | None = None,
    gravity: str | None = None,
    consequences: bool = True,
    plan: bool = True,
    last_action: str | None = None,
    seconds_per_action: float | None = None,
    seconds_per_row: float | None = None,
) -> dict[str, Any]:
    """The action-space message: what each action would do, one plan, and the time left.

    This is the play line's shape, and it is the reference implementation's
    (``third_party/Agent-JEV-Tetris-main/index.html:138-268``) for a reason it states plainly:
    the model is unreliable at counting, grids and arithmetic, and accuracy drops as irrelevant
    state grows — so the game measures every consequence and the model only judges. Two
    switches keep the *benchmark* line honest: ``consequences=False`` drops the computed
    numbers and ``plan=False`` drops the hint, because a benchmark that hands over either one
    measures whether the model can read, not whether it can decide.
    """
    from .simulator import (
        EMPTY,
        MASK_FILL,
        best_plan,
        column_heights,
        count_holes,
        bumpiness,
        dips_you_can_fill,
        drop,
        mask_cells,
        orientations_from_mask,
        outcome,
        row_gaps,
    )

    pieces = pieces or {}
    declared = mechanics or {}
    mask = _mask(pieces.get("current_piece_mask") or [], "current_piece_mask")
    motion = _motion(pieces.get("motion_vector"))
    board = ["".join(row) for row in board_matrix]
    direction = gravity or gravity_direction(motion, declared.get("gravity_direction"))
    playable = [
        [EMPTY if cell in {EMPTY, "0", "", None} else MASK_FILL for cell in row] for row in board
    ]
    positions = [[int(r), int(c)] for r, c in pieces.get("current_piece_positions") or []]
    shapes = list(orientations_from_mask(mask)) if mask else []
    current_cells = mask_cells(shapes[int(rotation) % len(shapes)]) if shapes else []
    start_column = (
        column if column is not None else (min(c for _, c in positions) if positions else 0)
    )

    target = (
        best_plan(playable, mask, rotation=int(rotation), column=start_column, gravity=direction)
        if (mask and plan)
        else None
    )
    foundation = (
        outcome(playable, current_cells, start_column, gravity=direction) if current_cells else None
    )
    rows_of_fall_left = None
    if foundation is not None and positions:
        bottom = max(row for row, _ in positions)
        rows_of_fall_left = max(0, foundation["lands_on_row"] - bottom)
    actions_left = None
    if rows_of_fall_left is not None and seconds_per_action and seconds_per_row:
        actions_left = max(1, int(rows_of_fall_left * (seconds_per_row / seconds_per_action)))

    candidates = []
    for action in ACTIONS:
        reach = _reach(
            playable, shapes, int(rotation), start_column, action, mask, drop, mask_cells, direction
        )
        if reach is None:
            continue
        cells, candidate_column = reach
        landed = outcome(playable, cells, candidate_column, gravity=direction)
        if landed is None:
            continue
        entry: dict[str, Any] = {"id": action, "does": ACTION_DOES[action]}
        if consequences:
            # Exactly the five advertised fields: what the message promises to say, no more.
            entry["then_lands"] = {
                key: landed[key]
                for key in ("in_columns", "on_row", "clears_lines", "new_holes", "roughness_after")
            }
        if action == ACTION_REVERSE.get(str(last_action)):
            entry["warning"] = (
                "This undoes your previous action: you end up back where you started."
            )
        if target is not None and action == target["next_action"]:
            entry["starts_the_plan"] = (
                f"The best landing found for this piece is columns {target['in_columns'][0]}-"
                f"{target['in_columns'][1]}, on row {target['lands_on_row']}, clearing "
                f"{target['clears_lines']} with {target['new_holes']} new holes. This action starts it."
            )
        candidates.append(entry)

    rules = (
        f"Tetris. The board is {len(board[0]) if board else 0} columns wide (0 = far left) and "
        f"{len(board)} rows tall (row 0 = top, row {len(board) - 1} = floor). One piece falls at a "
        f"time, and each turn you give it one action. GRAVITY: it drops one row at a time whether "
        f"you act or not. When it can fall no further it locks and the next piece starts at the top."
    )
    if actions_left is not None:
        rules += (
            f" The piece has about {rows_of_fall_left} rows of fall left, and you get roughly "
            f"{actions_left} action(s) before it lands — that is actions_left."
        )
    rules += (
        " A row with every square filled CLEARS: it scores, it vanishes, and everything above drops "
        "down. An empty square with a block above it is a HOLE: nothing can ever reach it. The game "
        "ends when the stack reaches the top."
    )

    from . import simulator as _sim

    heights = column_heights(playable)
    return {
        "task": TASK.format(columns=len(board[0]) if board else 0, rows=len(board)),
        "goal": GOAL,
        "rules": rules,
        "encoding": {**ENCODING, **ACTION_ENCODING} if consequences else dict(ENCODING),
        "board_matrix": board,
        "piece": {
            "mask": mask,
            "positions": positions,
            "orientations": [list(shape) for shape in shapes],
            "in_columns": sorted({c for _, c in positions}) if positions else [],
            "lands_on_row_if_untouched": None if foundation is None else foundation["lands_on_row"],
            "rows_of_fall_left": rows_of_fall_left,
            "actions_left": actions_left,
            "done_so_far": _done_so_far(pieces.get("done_so_far")),
        },
        "stack": {
            "column_heights": heights,
            "tallest": max(heights) if heights else 0,
            "holes": count_holes(playable),
            "roughness": bumpiness(playable),
            "dips_you_can_fill": dips_you_can_fill(playable),
            "rows_near_completing": [
                {
                    "row": gap["row"],
                    "needs": len(gap["empty_columns"]),
                    "columns": gap["empty_columns"],
                }
                for gap in row_gaps(playable)
            ],
        },
        "plan": target,
        "candidate_actions": candidates,
        "instructions": dict(ACTION_INSTRUCTIONS),
    }


def _reach(
    playable, shapes, rotation: int, column: int, action: str, mask, drop, mask_cells, gravity: str
):
    """The (cells, column) an action would leave the piece in, or None when it cannot be taken."""
    if not shapes:
        return None
    if action == "rotate":
        cells = mask_cells(shapes[(rotation + 1) % len(shapes)])
        return (
            (cells, column) if drop(playable, cells, column, gravity=gravity) is not None else None
        )
    if action in {"left", "right"}:
        delta = -1 if action == "left" else 1
        target = column + delta
        if target < 0 or target >= len(playable[0]):
            return None
        cells = mask_cells(shapes[rotation % len(shapes)])
        return (
            (cells, target) if drop(playable, cells, target, gravity=gravity) is not None else None
        )
    cells = mask_cells(shapes[rotation % len(shapes)])
    return (cells, column) if drop(playable, cells, column, gravity=gravity) is not None else None


def _done_so_far(history) -> str:
    if not history:
        return "nothing yet: the piece has just appeared"
    if isinstance(history, str):
        return history
    return ", ".join(str(item) for item in history)


def _candidate(
    entry: Any,
    *,
    board=None,
    mask=None,
    gravity: str = "down",
    results: bool = False,
    consequences: bool = False,
) -> dict[str, Any]:
    """One candidate move, with the id an answer has to name to be understood.

    With ``results`` the move also carries the board it would produce — the environment
    answering "what would happen", and nothing about whether that is good: no score, no
    holes, no height, no bumpiness, no best_candidate. That is the whole point of the arm:
    the model compares two states instead of reading a number someone else computed.
    """
    placement = entry[0] if isinstance(entry, (tuple, list)) else entry
    if isinstance(placement, dict):
        rotation, column = int(placement["rotation"]), int(placement["column"])
    else:
        rotation, column = int(placement.rotation), int(placement.column)
    move = {"id": f"r{rotation}c{column}", "rotation": rotation, "column": column}
    if consequences and board is not None and mask is not None:
        from .simulator import EMPTY, MASK_FILL, outcome

        cells = entry[1] if isinstance(entry, (tuple, list)) and len(entry) > 1 else None
        playable = [
            [EMPTY if cell in {"0", ".", ""} else MASK_FILL for cell in row] for row in board
        ]
        landed = outcome(playable, cells if cells is not None else mask, column, gravity=gravity)
        if landed is not None:
            # The five advertised numbers, and nothing that ranks them: the environment says
            # what would happen, the model says which of those is better (the C'/D' arms).
            move["then_lands"] = {
                key: landed[key]
                for key in ("in_columns", "on_row", "clears_lines", "new_holes", "roughness_after")
            }
    if results and board and mask:
        from .simulator import EMPTY, MASK_FILL, clear_lines, drop

        cells = entry[1] if isinstance(entry, (tuple, list)) and len(entry) > 1 else None
        playable = [
            [EMPTY if cell in {"0", ".", ""} else MASK_FILL for cell in row] for row in board
        ]
        shape = cells if cells is not None else mask
        dropped = (
            drop(playable, shape, column, gravity=gravity)
            if cells is not None
            else drop(playable, mask, column, gravity=gravity)
        )
        after = clear_lines(dropped)[0] if dropped is not None else None
        if after is not None:
            move["resulting_board"] = [
                "".join("0" if cell == EMPTY else "1" for cell in row) for row in after
            ]
    return move


def _motion(motion_vector) -> list[int] | None:
    if not motion_vector or len(motion_vector) != 2:
        return None
    return [int(motion_vector[0]), int(motion_vector[1])]


def _mask(mask, name: str) -> list[list[int]]:
    if mask is None or (isinstance(mask, str) and not mask):
        return []
    if isinstance(mask, str):
        raise ValueError(
            f"{name} must be a geometric mask (rows of 0/1), not a letter:"
            " a custom shape set has no letters to name shapes with"
        )
    return [[int(cell) for cell in row] for row in mask]


def _preview(next_preview):
    """The next piece as geometry, or ``None`` when the screen does not show one."""
    if next_preview is None:
        return None
    return _mask(next_preview, "next_preview")


__all__ = [
    "DEFAULT_GRAVITY",
    "DEFAULT_PIECE_SOURCE",
    "ENCODING",
    "EXECUTION",
    "GOAL",
    "TASK",
    "build_state",
    "gravity_direction",
]
