"""What the environment answers before the model is asked: outcomes, one plan, reachable aims.

These four functions are what turns the message from "here is a board, imagine the physics"
into "here is what each action would do". The reference implementation
(``third_party/Agent-JEV-Tetris-main``) documents four specific mistakes it made on the way
there, and each one is a test here, because each was a *silent* mis-ranking rather than a
crash:

1. **Zero-slide landings count** (``tetris.js:118-132``): its lookahead originally required a
   sideways step, so "stand the I bar upright right here" was invisible — the placement that
   matters most when a well is one column wide. ``best_plan`` enumerates landings directly, so
   a rotation-only landing is a candidate like any other;
2. **One plan, not one hint per direction** (README): attaching "head this way" to both left
   and right made the piece ping-pong until it landed. ``best_plan`` returns one landing and
   exactly one next action;
3. **Only advertise reachable aims** (``tetris.js:157-175``): a row whose gap has a block
   above it is sealed — nothing can ever reach it, so calling it "one square away" sends the
   decider chasing lines that cannot be made. ``row_gaps`` filters those;
4. **Depth of this piece, not height of the board** (``tetris.js:134-137``): a board-wide
   "tallest column" term does not move when you fill a valley, so it silently punishes the
   right play. The cost function uses the row the piece lands on.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from artemis.reflex.registry import load_skill

SKILLS = Path(__file__).resolve().parents[3] / "skills"
ROWS, COLUMNS = 20, 10


@pytest.fixture(scope="module")
def sim():
    load_skill("tetris", directory=SKILLS)
    return importlib.import_module("artemis_skills_tetris.simulator")


def board(rows: list[str]) -> list[list[str]]:
    """Rows of "0"/"1" (top first) as the simulator wants them."""
    assert len(rows) == ROWS, "a board is 20 rows"
    out = []
    for row in rows:
        assert len(row) == COLUMNS
        out.append(["." if cell == "0" else "#" for cell in row])
    return out


EMPTY_BOARD = board(["0" * COLUMNS] * ROWS)
#: A **one-column** well, five rows deep: only the upright bar can reach the floor, so a
#: lookahead that requires a sideways step would report this position as hopeless.
WELL = board(["0" * COLUMNS] * 15 + ["0111111111"] * 5)
#: Three columns wide and five deep: a flat bar also reaches the floor here.
WIDE_WELL = board(
    ["0" * COLUMNS] * 15 + ["0001111111", "0001111111", "0001111111", "0001111111", "0001111111"]
)
#: The same well plus a sealed gap: row 18 has an empty cell at column 4 with blocks above it.
SEALED = board(["0" * COLUMNS] * 16 + ["0001111111", "0000111111", "0001111111", "0001111111"])


# --------------------------------------------------------------------------- #
# outcome: what one action would do
# --------------------------------------------------------------------------- #


def test_an_outcome_reports_the_six_things_the_message_needs(sim):
    bar = sim.mask_cells([[1, 1, 1, 1]])
    outcome = sim.outcome(EMPTY_BOARD, bar, column=2, gravity="down")

    assert set(outcome) == {
        "in_columns",
        "on_row",
        "lands_on_row",
        "clears_lines",
        "new_holes",
        "roughness_after",
    }
    assert outcome["in_columns"] == (2, 5)
    assert outcome["lands_on_row"] == ROWS - 1
    assert outcome["clears_lines"] == 0 and outcome["new_holes"] == 0


def test_an_outcome_is_none_when_the_action_cannot_be_taken(sim):
    """An illegal step is not offered, and not guessed at either."""
    bar = sim.mask_cells([[1, 1, 1, 1]])
    assert sim.outcome(EMPTY_BOARD, bar, column=COLUMNS + 3, gravity="down") is None


def test_lands_on_row_is_how_deep_the_piece_gets(sim):
    """Bigger row = lower down = better: the reference's replacement for board height."""
    flat = sim.mask_cells([[1, 1, 1, 1]])
    upright = sim.mask_cells([[1], [1], [1], [1]])
    high = sim.outcome(WELL, upright, column=len(WELL[0]) - 1, gravity="down")
    deep = sim.outcome(WELL, upright, column=0, gravity="down")

    assert deep["lands_on_row"] > high["lands_on_row"], "the well is deeper than the surface"
    assert deep["lands_on_row"] == ROWS - 1, "into the well, onto the floor"
    assert deep["in_columns"] == (0, 0), "the bar stands upright in the well"
    assert sim.outcome(WELL, flat, column=0, gravity="down")["in_columns"] == (0, 3)


# --------------------------------------------------------------------------- #
# Pitfall 1: a landing that needs no sideways step
# --------------------------------------------------------------------------- #


def test_a_rotation_only_landing_is_a_candidate(sim):
    """Standing the bar up where it already is must be choosable, not invisible.

    The reference's lookahead required a sideways step before it evaluated anything, so the
    one placement that fills a one-column well never appeared in its options.
    """
    plan = sim.best_plan(WELL, [[1, 1, 1, 1]], gravity="down")

    assert plan is not None
    assert plan["in_columns"] == (0, 0), "the upright bar in the one-column well"
    assert plan["next_action"] == "rotate", "and turning on the spot is what starts it"


# --------------------------------------------------------------------------- #
# Pitfall 2: one plan, one next action
# --------------------------------------------------------------------------- #


def test_the_plan_is_single(sim):
    plan = sim.best_plan(WELL, [[1, 1, 1, 1]], gravity="down")
    assert isinstance(plan, dict)
    assert plan["next_action"] in {"left", "right", "rotate", "drop", "hold"}
    assert plan["actions_needed"] >= 1


def test_a_piece_already_on_its_landing_only_has_to_drop(sim):
    plan = sim.best_plan(EMPTY_BOARD, [[1, 1], [1, 1]], gravity="down")
    assert plan["next_action"] in {"drop", "hold"}, "nothing to turn or slide"


# --------------------------------------------------------------------------- #
# Pitfall 3: sealed gaps are not aims
# --------------------------------------------------------------------------- #


def test_a_sealed_gap_is_not_advertised_as_a_row_near_completing(sim):
    """In row 18 column 3 is empty with a block above it: nothing can ever drop in there.

    Row 18 is still advertised for the columns that *are* open (0-2) — the point is that the
    sealed one is left out, because calling it "one square away" sends the decider chasing a
    line that cannot be made.
    """
    gaps = sim.row_gaps(SEALED)
    by_row = {gap["row"]: gap for gap in gaps}
    assert 18 in by_row, "the row is reachable through its open columns"
    assert 3 not in by_row[18]["empty_columns"], "column 3 is sealed under a block"
    assert by_row[18]["empty_columns"] == [0, 1, 2]


def test_an_open_gap_is_advertised(sim):
    gaps = sim.row_gaps(WIDE_WELL)
    by_row = {gap["row"]: gap for gap in gaps}
    assert 15 in by_row, "the wide well's top row is open above"
    assert by_row[15]["empty_columns"] == [0, 1, 2]


# --------------------------------------------------------------------------- #
# Pitfall 4: the depth of this piece, not the height of the board
# --------------------------------------------------------------------------- #


def test_filling_a_valley_is_not_punished(sim):
    """The right play in a well must not rank below stacking on the surface.

    A board-wide "tallest column" term does not change when a valley is filled — it stayed at
    5 rows here either way — so it cannot see that one option is strictly better. The cost
    function uses the landed row instead, and the upright bar in the well wins.
    """
    plan = sim.best_plan(WELL, [[1, 1, 1, 1]], gravity="down")
    surface = sim.outcome(WELL, sim.mask_cells([[1, 1, 1, 1]]), column=6, gravity="down")

    assert plan["in_columns"] == (0, 0), "the one-column well is the plan"
    assert surface["lands_on_row"] == ROWS - 6, "left on the surface it sits five rows higher"
    # And the metric the reference measured as harmful cannot see the difference at all: the
    # board's tallest column is set by the *surface*, so a one-column well and a three-column
    # one read the same — which is exactly why `lands_on_row` replaced it.
    assert max(sim.column_heights(WELL)) == max(sim.column_heights(WIDE_WELL)) == ROWS - 15


def test_clearing_a_row_beats_not_clearing_it(sim):
    """A move that completes a row must win over one that does not."""
    # Row 19 needs exactly four more cells: the bar fills columns 6-9 and completes it.
    nearly_full = board(["0" * COLUMNS] * 19 + ["1111110000"])
    plan = sim.best_plan(nearly_full, [[1, 1, 1, 1]], gravity="down")
    assert plan["clears_lines"] >= 1, "completing the row must beat not completing it"
    assert plan["in_columns"] == (6, 9), "flat along the bottom, filling the four empty cells"


def test_the_dips_say_which_pieces_still_fit(sim):
    """Reachability of an aim is decided by simulation, not by a width-and-depth rule."""
    dips = sim.dips_you_can_fill(WELL)
    assert dips, "the one-column well is a dip"
    well = next(dip for dip in dips if dip["column"] == 0)
    assert "I" in well["fits"], "an upright bar fits a one-column well"
    assert well["depth"] >= 1
    assert well["floor_row"] == ROWS - 1
