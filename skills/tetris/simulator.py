"""Tetris rules as pure code: the reference the device is compared against.

This is a *skill* module, not framework code: it knows the seven tetrominoes, the
rotation states, gravity falling, line clears and the features a policy scores.
The device's pixels are somebody else's problem (``perception.py``).

Why a simulator at all, when the game does the simulating? Because a policy must
be able to evaluate a move *before* making it, and because a search policy and a
model policy need the same legality boundary: only placements this module declares
legal are ever offered to a policy, so no policy can produce an illegal move.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Board geometry of the *reference* rules (the skill's calibration may differ).
BOARD_WIDTH = 10
BOARD_HEIGHT = 20

#: The seven tetrominoes in their spawn orientation, as cell offsets (x, y) with
#: y growing downward from the top of the board.
SHAPES: dict[str, tuple[tuple[int, int], ...]] = {
    "I": ((0, 0), (1, 0), (2, 0), (3, 0)),
    "O": ((0, 0), (1, 0), (0, 1), (1, 1)),
    "T": ((0, 0), (1, 0), (2, 0), (1, 1)),
    "S": ((1, 0), (2, 0), (0, 1), (1, 1)),
    "Z": ((0, 0), (1, 0), (1, 1), (2, 1)),
    "J": ((0, 0), (0, 1), (1, 1), (2, 1)),
    "L": ((2, 0), (0, 1), (1, 1), (2, 1)),
}

PIECE_NAMES = tuple(SHAPES)

#: The directions a piece can fall in. The game this skill plays can switch gravity
#: mid-session, so it is a parameter of the rules, not a constant of them.
GRAVITIES = ("down", "up", "left", "right")
#: gravity → (axis it moves along, step). "y" is rows (top to bottom), "x" is columns.
_GRAVITY_STEP = {"down": ("y", 1), "up": ("y", -1), "right": ("x", 1), "left": ("x", -1)}
#: What a mask-placed piece leaves behind. A letter names a shape from the *standard*
#: seven, which a game with custom shapes does not have, so geometry gets its own
#: marker rather than a lie.
MASK_FILL = "#"

EMPTY = "."
#: A filled cell keeps the letter of the piece that filled it, so a frame parsed by
#: perception can be compared with a simulation cell by cell.
Cell = str
Board = list[list[Cell]]


def empty_board(width: int = BOARD_WIDTH, height: int = BOARD_HEIGHT) -> Board:
    return [[EMPTY] * width for _ in range(height)]


def clone(board: Board) -> Board:
    return [row[:] for row in board]


def rotate(cells: tuple[tuple[int, int], ...]) -> tuple[tuple[int, int], ...]:
    """Rotate one shape clockwise inside its own bounding box."""
    if not cells:
        return cells
    width = max(x for x, _ in cells) + 1
    return tuple(sorted((width - 1 - y, x) for x, y in cells))


def orientations(piece: str) -> tuple[tuple[tuple[int, int], ...], ...]:
    """Every distinct rotation of ``piece`` (``O`` and ``I`` collapse fewer)."""
    cells = SHAPES[piece]
    seen: list[tuple[tuple[int, int], ...]] = []
    for _ in range(4):
        cells = rotate(cells)
        normalised = normalise(cells)
        if normalised not in seen:
            seen.append(normalised)
    return tuple(seen)


def normalise(cells: tuple[tuple[int, int], ...]) -> tuple[tuple[int, int], ...]:
    """Shift a shape so its minimum x and y are 0 (stable comparison)."""
    min_x = min(x for x, _ in cells)
    min_y = min(y for _, y in cells)
    return tuple(sorted((x - min_x, y - min_y) for x, y in cells))


def shape_width(cells: tuple[tuple[int, int], ...]) -> int:
    return max(x for x, _ in cells) + 1


def shape_height(cells: tuple[tuple[int, int], ...]) -> int:
    return max(y for _, y in cells) + 1


# --------------------------------------------------------------------------- #
# Shapes as geometry: any mask, not the standard seven
# --------------------------------------------------------------------------- #


def mask_cells(mask) -> tuple[tuple[int, int], ...]:
    """Any accepted mask form → normalised ``(x, y)`` cell offsets.

    Accepted, because all three are produced somewhere in this skill:

    * a piece **letter** (``"T"``) — the convenience the arena and the search policy
      still use;
    * a sequence of **2-tuples** of offsets — what ``SHAPES``/``orientations`` produce;
    * a 2-D **bitmap** of 0/1 — what ``pieces.split_pieces`` reads off the screen, and
      what a custom shape in the calibration file looks like;
    * a 2-D numpy array — a bitmap.

    A list of tuples is offsets; a list of lists is a bitmap. That is the only
    ambiguous case, and every producer in this codebase sits on one side of it.
    """
    if isinstance(mask, str):
        if mask not in SHAPES:
            raise ValueError(f"unknown piece {mask!r}; known: {', '.join(PIECE_NAMES)}")
        return SHAPES[mask]

    rows = None
    if getattr(mask, "ndim", None) == 2:  # numpy array
        rows = [list(row) for row in mask.tolist()]
    elif isinstance(mask, (list, tuple)):
        items = list(mask)
        if not items:
            raise ValueError("an empty mask describes no piece")
        if all(isinstance(item, tuple) and len(item) == 2 for item in items):
            return normalise(tuple((int(x), int(y)) for x, y in items))
        rows = [list(item) for item in items]

    if rows is None:
        raise ValueError(f"cannot read a piece shape out of {type(mask).__name__}")

    cells = tuple(
        (column, row)
        for row, line in enumerate(rows)
        for column, cell in enumerate(line)
        if int(cell)
    )
    if not cells:
        raise ValueError("the mask has no filled cell")
    return normalise(cells)


def mask_bitmap(mask) -> list[list[int]]:
    """The same shape as a 0/1 bitmap of its own bounding box."""
    cells = mask_cells(mask)
    width, height = shape_width(cells), shape_height(cells)
    bitmap = [[0] * width for _ in range(height)]
    for x, y in cells:
        bitmap[y][x] = 1
    return bitmap


def orientations_from_mask(mask) -> tuple[list[list[int]], ...]:
    """Every distinct rotation of a mask, **starting with the mask as given**.

    Index 0 is the orientation the screen showed, because that is what the control
    layer needs: it turns ``rotation`` into that many taps, and tap zero must mean
    "leave it as it is". (``orientations`` starts from the *spawn* shape instead,
    which is the right convention for a piece that has just appeared.)
    """
    first = mask_cells(mask)
    seen: list[tuple[tuple[int, int], ...]] = [first]
    current = first
    for _ in range(3):
        current = normalise(rotate(current))
        if current not in seen:
            seen.append(current)
    return tuple(mask_bitmap(cells) for cells in seen)


@dataclass(frozen=True)
class Placement:
    """One legal way to drop a piece: rotation index + column of its left edge."""

    rotation: int
    column: int

    def as_dict(self) -> dict[str, int]:
        return {"rotation": self.rotation, "column": self.column}


def can_place(board: Board, piece: str, cells: tuple[tuple[int, int], ...], column: int) -> bool:
    """Whether ``piece`` fits at the *entry* position of a downward drop (top-out is no)."""
    _ = piece  # kept in the signature: callers read "place this piece" better than a mask
    return _fits(board, cells, column, 0)


def _fits(board: Board, cells: tuple[tuple[int, int], ...], x_offset: int, y_offset: int) -> bool:
    """Whether the shape fits at one exact position, inside the board and on empty cells."""
    height, width = len(board), len(board[0])
    for x, y in cells:
        bx, by = x_offset + x, y_offset + y
        if bx < 0 or by < 0 or bx >= width or by >= height:
            return False
        if board[by][bx] != EMPTY:
            return False
    return True


def _gravity(direction) -> tuple[str, int]:
    axis_step = _GRAVITY_STEP.get(str(direction or "down").lower())
    if axis_step is None:
        raise ValueError(f"unknown gravity {direction!r}; expected one of {', '.join(GRAVITIES)}")
    return axis_step


def _resolve_drop(mask, second, column) -> tuple[tuple[tuple[int, int], ...], int]:
    """``(cells, coordinate)`` from either the new call form or the legacy one.

    ``drop(board, mask, column)`` is the interface; ``drop(board, piece, cells, column)``
    is what every existing caller (the arena, the search policy, the tests) writes, and
    in that form the ``cells`` argument is authoritative — it carries the rotation.
    """
    if column is None:
        if second is None:
            raise TypeError("drop() needs the placement coordinate")
        return mask_cells(mask), int(second)
    if second is None:
        return mask_cells(mask), int(column)
    return mask_cells(second), int(column)


def drop(
    board: Board,
    mask,
    second=None,
    column=None,
    *,
    gravity: str = "down",
) -> Board | None:
    """Let a piece fall in ``gravity`` until it blocks; ``None`` when it cannot enter.

    ``mask`` is any shape :func:`mask_cells` accepts (a letter, cell offsets, or a 0/1
    bitmap), and ``column`` is its position along the axis *perpendicular* to gravity:
    the column of its left edge under ``down``/``up``, the row of its top edge under
    ``left``/``right``. Both integer arguments are bounds-checked, so a mask that
    cannot fit on the board at all returns ``None`` rather than wrapping.

    The piece enters from the side gravity comes from and slides until the next step
    would collide — which is the same rule in all four directions, and the reason the
    old downward behaviour is bit-for-bit what it was.
    """
    cells, coordinate = _resolve_drop(mask, second, column)
    fill = mask if isinstance(mask, str) else MASK_FILL
    axis, step = _gravity(gravity)
    cells = normalise(cells)
    height, width = len(board), len(board[0])

    if axis == "y":
        if coordinate < 0 or coordinate + shape_width(cells) > width:
            return None
        limit = height - shape_height(cells)
        position_fits = lambda offset: _fits(board, cells, coordinate, offset)  # noqa: E731
    else:
        if coordinate < 0 or coordinate + shape_height(cells) > height:
            return None
        limit = width - shape_width(cells)
        position_fits = lambda offset: _fits(board, cells, offset, coordinate)  # noqa: E731

    if limit < 0:
        return None
    entry = 0 if step > 0 else limit
    if not position_fits(entry):
        return None
    offset = entry
    while 0 <= offset + step <= limit and position_fits(offset + step):
        offset += step

    result = clone(board)
    for x, y in cells:
        bx, by = (coordinate + x, offset + y) if axis == "y" else (offset + x, coordinate + y)
        result[by][bx] = fill
    return result


def clear_lines(board: Board) -> tuple[Board, int]:
    """Remove full rows and drop everything above them down."""
    width = len(board[0])
    kept = [row for row in board if any(cell == EMPTY for cell in row)]
    cleared = len(board) - len(kept)
    for _ in range(cleared):
        kept.insert(0, [EMPTY] * width)
    return kept, cleared


def column_heights(board: Board) -> list[int]:
    heights = []
    for x in range(len(board[0])):
        height = 0
        for y in range(len(board)):
            if board[y][x] != EMPTY:
                height = len(board) - y
                break
        heights.append(height)
    return heights


def count_holes(board: Board) -> int:
    """Empty cells with a filled cell somewhere above them in the same column."""
    holes = 0
    for x in range(len(board[0])):
        seen_block = False
        for y in range(len(board)):
            if board[y][x] != EMPTY:
                seen_block = True
            elif seen_block:
                holes += 1
    return holes


def bumpiness(board: Board) -> int:
    heights = column_heights(board)
    return sum(abs(heights[i] - heights[i + 1]) for i in range(len(heights) - 1))


def aggregate_height(board: Board) -> int:
    return sum(column_heights(board))


def row_transitions(board: Board) -> int:
    """Horizontal transitions between filled and empty (walls count as filled)."""
    transitions = 0
    for row in board:
        previous = True
        for cell in row:
            filled = cell != EMPTY
            if filled != previous:
                transitions += 1
            previous = filled
        if not previous:
            transitions += 1
    return transitions


def column_transitions(board: Board) -> int:
    transitions = 0
    width = len(board[0])
    for x in range(width):
        previous = True
        for y in range(len(board)):
            filled = board[y][x] != EMPTY
            if filled != previous:
                transitions += 1
            previous = filled
        if not previous:
            transitions += 1
    return transitions


def well_sums(board: Board) -> int:
    """Depth of the wells: columns lower than both neighbours."""
    heights = column_heights(board)
    total = 0
    for x, height in enumerate(heights):
        left = heights[x - 1] if x > 0 else len(board)
        right = heights[x + 1] if x + 1 < len(heights) else len(board)
        if height < left and height < right:
            total += min(left, right) - height
    return total


def landing_height(board: Board, piece: str) -> int:
    """Height above the floor of the piece's lowest cell (0 = resting on the floor)."""
    for y in range(len(board) - 1, -1, -1):
        if any(cell == piece for cell in board[y]):
            return len(board) - y
    return len(board)


def legal_placements(
    board: Board,
    mask,
    *,
    gravity: str = "down",
    rotations: int | None = None,
) -> list[tuple[Placement, tuple[tuple[int, int], ...]]]:
    """Every rotation/position pair this shape may legally be played in ``gravity``.

    This is the legality boundary the plan insists on: a policy — including a model —
    only ever chooses *among* these, so an illegal move is impossible by construction.

    A letter keeps the spawn-shape orientation order (``orientations``), a geometric
    mask keeps its own (``orientations_from_mask``, index 0 = as seen).
    """
    axis, _step = _gravity(gravity)
    shapes = orientations(mask) if isinstance(mask, str) else orientations_from_mask(mask)
    limit = (len(board[0]) if axis == "y" else len(board)) - 1
    out: list[tuple[Placement, tuple[tuple[int, int], ...]]] = []
    for index, shape in enumerate(shapes):
        if rotations is not None and index >= rotations:
            break
        cells = mask_cells(shape)
        span = shape_width(cells) if axis == "y" else shape_height(cells)
        for coordinate in range(limit - span + 2):
            if drop(board, cells, coordinate, gravity=gravity) is not None:
                out.append((Placement(rotation=index, column=coordinate), cells))
    return out


# --------------------------------------------------------------------------- #
# What the environment answers: outcomes, one plan, reachable aims
#
# The reference implementation (third_party/Agent-JEV-Tetris-main) keeps this half in the
# game and hands the model only judgements: "the game measures every consequence in code and
# jev only ever judges pre-computed outcomes" (README), because the model is unreliable at
# counting, grids and arithmetic, and accuracy drops as irrelevant state grows. These are our
# half of that split, with the four mistakes that reference documents (zero-slide landings,
# one hint per direction, sealed gaps, board height instead of piece depth) designed out.
# --------------------------------------------------------------------------- #

#: How the plan's cost ranks a landing, in the reference's spirit (``tetris.js:134-137``):
#: holes dominate, then cleared lines, then depth, then roughness. Its ``tallest_column``
#: term is deliberately absent — measured there as harmful, because filling a valley does not
#: move it, so it cannot tell the right play from stacking on the surface.
PLAN_HOLE_COST = 1000
PLAN_LINE_REWARD = 200
PLAN_DEPTH_REWARD = 15


def _fill(board) -> Board:
    """The board as EMPTY/MASK_FILL, which ``drop``/``clear_lines``/the metrics expect."""
    return [
        [EMPTY if cell in {EMPTY, "0", "", None} else MASK_FILL for cell in row] for row in board
    ]


def outcome(board, cells, column: int, *, gravity: str = "down") -> dict | None:
    """What happens if this shape is played in ``column``: the numbers the message needs.

    ``cells`` are one shape's cells (``mask_cells`` of whatever orientation is being judged)
    and ``column`` is where its left edge goes. ``None`` when it does not fit — an action that
    cannot be taken is not advertised, and not guessed at either.

    ``lands_on_row`` is the deepest row the piece settles on (bigger = lower = better): the
    reference's replacement for a board-wide "tallest column".
    """
    if not cells:
        return None
    filled = _fill(board)
    dropped = drop(filled, cells, column, gravity=gravity)
    if dropped is None:
        return None
    settled, cleared = clear_lines(dropped)
    landed = [
        (row, col)
        for row, line in enumerate(dropped)
        for col, cell in enumerate(line)
        if filled[row][col] == EMPTY and cell != EMPTY
    ]
    if not landed:
        return None
    # The simulator's cells are (x, y): the first coordinate is the column. `drop` already
    # placed the shape, so the landed cells come from the board diff above and the columns are
    # simply the width it spans at `column`.
    x_offsets = [x for x, _ in cells]
    in_columns = (column + min(x_offsets), column + max(x_offsets))
    return {
        "in_columns": in_columns,
        "on_row": max(row for row, _ in landed),
        "lands_on_row": max(row for row, _ in landed),
        "clears_lines": cleared,
        "new_holes": count_holes(_fill(settled)) - count_holes(filled),
        "roughness_after": bumpiness(_fill(settled)),
    }


def plan_cost(result: dict) -> float:
    """Lower is better: fewer new holes, more cleared lines, deeper, flatter."""
    return (
        result["new_holes"] * PLAN_HOLE_COST
        - result["clears_lines"] * PLAN_LINE_REWARD
        - result["lands_on_row"] * PLAN_DEPTH_REWARD
        + result["roughness_after"]
    )


def best_plan(
    board,
    mask,
    *,
    rotation: int = 0,
    column: int | None = None,
    gravity: str = "down",
) -> dict | None:
    """The one landing this piece should aim for, and the single action that starts it.

    One plan, not one hint per direction: the reference attached "head this way" to both left
    and right, they pointed opposite ways, and the piece ping-ponged until it landed.

    Landings are enumerated directly over every orientation and column, which is what makes a
    **rotation-only** landing (no sideways step) an ordinary candidate — the case the
    reference's first lookahead could not see, and the one that matters when a well is one
    column wide. Reachability beyond that (a mid-air pivot the shape cannot fit) is left to
    the device, which re-measures the column every frame.
    """
    shapes = orientations_from_mask(mask) if not isinstance(mask, str) else orientations(mask)
    best: dict | None = None
    for index, shape in enumerate(shapes):
        cells = mask_cells(shape)
        width = shape_width(cells)
        for column_candidate in range(len(board[0]) - width + 1):
            result = outcome(board, cells, column_candidate, gravity=gravity)
            if result is None:
                continue
            candidate = {**result, "rotation": index}
            if best is None or plan_cost(candidate) < plan_cost(best):
                best = candidate
    if best is None:
        return None

    turns = (best["rotation"] - int(rotation)) % max(1, len(shapes))
    delta = 0 if column is None else best["in_columns"][0] - int(column)
    if turns:
        next_action = "rotate"
    elif delta < 0:
        next_action = "left"
    elif delta > 0:
        next_action = "right"
    else:
        next_action = "drop"
    best["next_action"] = next_action
    best["actions_needed"] = turns + abs(delta) + 1
    best["columns"] = best["in_columns"]
    return best


def row_gaps(board) -> list[dict]:
    """Rows that are partly filled and **still reachable**, nearest-to-complete first.

    A gap with any block above it is sealed: nothing can ever drop into it, so advertising it
    as "one square away" sends the decider chasing lines that cannot be made (the reference's
    ``rowGaps``, ``tetris.js:157-175``).
    """
    filled = _fill(board)
    gaps: list[dict] = []
    for row, line in enumerate(filled):
        empty = [column for column, cell in enumerate(line) if cell == EMPTY]
        if not empty or len(empty) == len(line):
            continue
        open_columns = [
            column
            for column in empty
            if not any(filled[above][column] != EMPTY for above in range(row))
        ]
        if open_columns:
            gaps.append({"row": row, "empty_columns": open_columns})
    return sorted(gaps, key=lambda gap: len(gap["empty_columns"]))


def dips_you_can_fill(board, *, pieces: tuple[str, ...] = PIECE_NAMES) -> list[dict]:
    """Surface dips, and which pieces still fit each one — decided by simulation.

    The reference reports the same thing (``dips_you_can_fill``) rather than a width-and-depth
    rule of thumb: "fits cleanly" is a question about actual shapes, so it is answered by
    trying them.
    """
    filled = _fill(board)
    heights = column_heights(filled)
    floor = len(filled) - 1
    dips: list[dict] = []
    column = 0
    while column < len(heights):
        run_end = column
        while run_end + 1 < len(heights) and heights[run_end + 1] == heights[column]:
            run_end += 1
        neighbours = [heights[c] for c in (column - 1, run_end + 1) if 0 <= c < len(heights)]
        depth = (max(neighbours) - heights[column]) if neighbours else 0
        if depth > 0:
            run = list(range(column, run_end + 1))
            floor_row = floor if heights[column] == 0 else floor - (heights[column] - 1)
            # The dip's own floor cells: the empty cell of each column in the run, at its top.
            dip_cells = {(floor - heights[c], c) for c in run}
            fits = [
                name
                for name in pieces
                if any(
                    dip_cells & set(_landed_cells_of(filled, name, candidate))
                    for candidate in _placements_of(filled, name)
                )
            ]
            dips.append(
                {
                    "column": column,
                    "columns": run,
                    "depth": depth,
                    "floor_row": floor_row,
                    "fits": fits,
                }
            )
        column = run_end + 1
    return sorted(dips, key=lambda dip: -dip["depth"])


def _placements_of(board, name: str):
    return [(cells, placement.column) for placement, cells in legal_placements(board, name)]


def _landed_cells_of(board, name: str, entry) -> tuple[tuple[int, int], ...]:
    cells, column = entry
    dropped = drop(board, cells, column)
    if dropped is None:
        return ()
    return tuple(
        (row, col)
        for row, line in enumerate(dropped)
        for col, cell in enumerate(line)
        if board[row][col] == EMPTY and cell != EMPTY
    )


__all__ = [
    "BOARD_HEIGHT",
    "BOARD_WIDTH",
    "EMPTY",
    "GRAVITIES",
    "MASK_FILL",
    "PIECE_NAMES",
    "SHAPES",
    "Board",
    "Placement",
    "aggregate_height",
    "bumpiness",
    "can_place",
    "clear_lines",
    "clone",
    "column_heights",
    "column_transitions",
    "count_holes",
    "drop",
    "empty_board",
    "landing_height",
    "best_plan",
    "dips_you_can_fill",
    "legal_placements",
    "outcome",
    "plan_cost",
    "row_gaps",
    "mask_bitmap",
    "mask_cells",
    "normalise",
    "orientations",
    "orientations_from_mask",
    "rotate",
    "row_transitions",
    "shape_height",
    "shape_width",
    "well_sums",
]
