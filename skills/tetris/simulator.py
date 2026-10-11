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


@dataclass(frozen=True)
class Placement:
    """One legal way to drop a piece: rotation index + column of its left edge."""

    rotation: int
    column: int

    def as_dict(self) -> dict[str, int]:
        return {"rotation": self.rotation, "column": self.column}


def can_place(board: Board, piece: str, cells: tuple[tuple[int, int], ...], column: int) -> bool:
    """Whether ``piece`` fits when dropped into ``column`` (top-out counts as no)."""
    height = len(board)
    if column < 0 or column + shape_width(cells) > len(board[0]):
        return False
    for x, y in cells:
        bx, by = column + x, y
        if by >= height:
            return False
        if board[by][bx] != EMPTY:
            return False
    return True


def drop(board: Board, piece: str, cells: tuple[tuple[int, int], ...], column: int) -> Board | None:
    """Drop ``piece`` in ``column`` and return the resulting board (``None`` if it cannot)."""
    if not can_place(board, piece, cells, column):
        return None
    height = len(board)
    rows = [y for _, y in cells]
    top = min(rows)
    # Fall until the next step would collide: gravity is one row at a time.
    offset = 0
    while True:
        shifted = tuple((x, y + offset + 1) for x, y in cells)
        blocked = any(by >= height or board[by][column + bx] != EMPTY for bx, by in shifted)
        if blocked:
            break
        offset += 1
    result = clone(board)
    for x, y in cells:
        result[y + offset][column + x] = piece
    _ = top  # the spawn row is implied by the shape; kept for clarity
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
    board: Board, piece: str, *, rotations: int | None = None
) -> list[tuple[Placement, tuple[tuple[int, int], ...]]]:
    """Every rotation/column pair this piece may legally be dropped at."""
    out: list[tuple[Placement, tuple[tuple[int, int], ...]]] = []
    for rotation, cells in enumerate(orientations(piece)):
        if rotations is not None and rotation >= rotations:
            break
        for column in range(len(board[0]) - shape_width(cells) + 1):
            if can_place(board, piece, cells, column):
                out.append((Placement(rotation=rotation, column=column), cells))
    return out


__all__ = [
    "BOARD_HEIGHT",
    "BOARD_WIDTH",
    "EMPTY",
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
    "legal_placements",
    "normalise",
    "orientations",
    "rotate",
    "row_transitions",
    "shape_width",
    "well_sums",
]
