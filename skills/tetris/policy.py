"""Search policy: score every legal placement, take the best, look one piece ahead.

Deterministic, offline-testable, and the loop's fallback when a model-driven
policy is slow, rate-limited or unavailable. Weights come from calibration so a
game with different scoring or a different risk appetite can retune without code.

Features are the classic Dellacherie/El-Tetris set — they are the reason a plain
search plays well: holes dominate, height and bumpiness keep the stack low and
flat, transitions avoid spiky surfaces, clears are rewarded.
"""

from __future__ import annotations

from artemis.reflex.contract import Action

from . import stall
from .simulator import (
    Board,
    EMPTY,
    Placement,
    aggregate_height,
    bumpiness,
    clear_lines,
    clone,
    column_transitions,
    count_holes,
    drop,
    legal_placements,
    row_transitions,
    well_sums,
)

#: Default weights, tuned for the standard rules (see the skill README).
DEFAULT_WEIGHTS: dict[str, float] = {
    "lines": 1.6,
    "holes": -6.0,
    "landing_height": -0.6,
    "aggregate_height": -0.35,
    "bumpiness": -0.25,
    "row_transitions": -0.25,
    "column_transitions": -0.25,
    "well_sums": -0.2,
}


def score_board(board: Board, cleared: int, weights: dict[str, float]) -> float:
    return (
        weights.get("lines", 0.0) * cleared
        + weights.get("holes", 0.0) * count_holes(board)
        + weights.get("aggregate_height", 0.0) * aggregate_height(board)
        + weights.get("bumpiness", 0.0) * bumpiness(board)
        + weights.get("row_transitions", 0.0) * row_transitions(board)
        + weights.get("column_transitions", 0.0) * column_transitions(board)
        + weights.get("well_sums", 0.0) * well_sums(board)
    )


def evaluate(
    board: Board,
    piece: str,
    cells: tuple[tuple[int, int], ...],
    weights: dict[str, float],
    *,
    landing_height: float,
) -> tuple[float, int]:
    """Score one placement: the resulting board plus how many lines it clears."""
    dropped = drop(board, piece, cells, 0)
    if dropped is None:  # pragma: no cover - callers only pass legal placements
        return float("-inf"), 0
    _, mid = clear_lines(dropped)
    return score_board(dropped, mid, weights), mid


def choose(
    board: Board,
    piece: str,
    *,
    weights: dict[str, float] | None = None,
    next_piece: str | None = None,
) -> tuple[Placement | None, dict[str, float]]:
    """The best legal placement for ``piece`` (one-ply lookahead when given ``next``)."""
    active = dict(DEFAULT_WEIGHTS if weights is None else weights)
    candidates = legal_placements(board, piece)
    if not candidates:
        return None, {}

    best: tuple[float, Placement] | None = None
    detail: dict[str, float] = {}
    for placement, cells in candidates:
        dropped = drop(board, piece, cells, placement.column)
        if dropped is None:
            continue
        settled, cleared = clear_lines(dropped)
        total = score_board(settled, cleared, active)
        total += active.get("landing_height", 0.0) * _landing_height(settled, piece)
        if next_piece:
            total += 0.6 * _best_reply(settled, next_piece, active)
        if best is None or total > best[0]:
            best = (total, placement)
            detail = {"score": round(total, 3), "cleared": float(cleared)}
    return (best[1] if best else None), detail


def _landing_height(board: Board, piece: str) -> float:
    """How high the piece's lowest cell sits (0 = on the floor)."""
    for y in range(len(board) - 1, -1, -1):
        if any(cell == piece for cell in board[y]):
            return float(len(board) - y)
    return float(len(board))


def _best_reply(board: Board, piece: str, weights: dict[str, float]) -> float:
    """Best score the *next* piece could reach — a cheap one-ply lookahead."""
    best = float("-inf")
    for placement, cells in legal_placements(board, piece):
        dropped = drop(board, piece, cells, placement.column)
        if dropped is None:
            continue
        settled, cleared = clear_lines(dropped)
        best = max(best, score_board(settled, cleared, weights))
    return 0.0 if best == float("-inf") else best


def piece_column(state) -> int | None:
    """The falling piece's current column, from whichever state shape this is.

    The control layer turns "current column -> chosen column" into a horizontal swipe, so
    a decision that does not carry the current column is a decision the device cannot
    execute: it rotates and drops where the piece already is. The measured pipeline knows
    the piece's cells (``current_piece_positions``, rows of ``[row, column]``); the older
    letter path knows them as ``falling_cells`` (``(x, y)``).
    """
    positions = state.get("current_piece_positions") or []
    if positions:
        return min(int(cell[1]) for cell in positions)
    falling = state.get("falling_cells") or []
    if falling:
        return min(int(cell[0]) for cell in falling)
    return None


# --------------------------------------------------------------------------- #
# The skill entry points
# --------------------------------------------------------------------------- #


def decide(state, calibration):
    """``policy.decide(state, calibration) -> Action`` — the reflex runtime's contract.

    ``state`` is what ``perception.parse`` produced: board matrix, current piece and
    (if the app shows it) the next piece. ``done`` is set when the session should end:
    the game is over, or the caller's target was reached.
    """
    calibration = calibration or {}
    counters = state.get("counters") if isinstance(state, dict) else {}
    counters = dict(counters or {})
    lines = int(state.get("lines") or counters.get("lines") or 0)
    target_lines = int(calibration.get("target_lines") or 0)
    if state.get("game_over"):
        return Action(done=True, state={"counters": counters, "note": "game over"})
    if target_lines and lines >= target_lines:
        counters["target_reached"] = target_lines
        return Action(done=True, state={"counters": counters})
    stopped = stall.stalled(state)
    if stopped:
        # Same rule as the Jev policy: the playfield has stopped changing, so there is
        # nothing to decide; the note carries the measurement, not a verdict.
        return Action(
            done=True,
            state={"counters": counters, "note": "board static", "stall": stopped},
        )

    board = state.get("board") or []
    piece = state.get("current")
    if not board or not piece:
        return Action()  # blind-ish frame: no move, let the runtime keep counting

    placement, detail = choose(
        board,
        piece,
        weights=calibration.get("weights"),
        next_piece=state.get("next"),
    )
    if placement is None:
        return Action(done=True, state={"counters": {**counters, "topped_out": 1}})

    counters["decisions"] = int(counters.get("decisions") or 0) + 1
    counters.setdefault("last_cleared", detail.get("cleared", 0))
    return Action(
        gestures=(),
        done=False,
        state={
            "placement": placement.as_dict(),
            "score": detail.get("score"),
            "counters": counters,
            "current_column": piece_column(state),
            "board_rows": len(board),
            "filled": sum(1 for row in board for cell in row if cell != EMPTY),
        },
        note=f"{piece}→r{placement.rotation}c{placement.column}",
    )


__all__ = ["DEFAULT_WEIGHTS", "choose", "decide", "evaluate", "piece_column", "score_board"]
