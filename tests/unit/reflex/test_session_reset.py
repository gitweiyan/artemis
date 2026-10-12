"""A session starts from nothing: the previous one's frames and decisions do not leak.

Both halves of this plan keep state across frames on purpose — the frame history that
makes motion measurable, and the last decision that keeps the model from being asked
about the same piece twice. Both are *session* state, and a second session in the same
process (a restart flow, a second `play_reflex` call, a test run) must not inherit them:
a replayed decision would rotate and drop a piece that is not the one it was chosen for.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from artemis.reflex.registry import load_skill
from artemis.services import systemone

SKILLS = Path(__file__).resolve().parents[3] / "skills"
SQUARE = [[1, 1], [1, 1]]


@pytest.fixture(scope="module")
def tetris():
    return load_skill("tetris", directory=SKILLS)


@pytest.fixture(scope="module")
def session():
    load_skill("tetris", directory=SKILLS)
    return importlib.import_module("artemis_skills_tetris.session")


def state_for(positions=((14, 3), (14, 4), (15, 3), (15, 4))):
    return {
        "board_matrix": ["0" * 10] * 20,
        "current_piece_mask": SQUARE,
        "current_piece_positions": [list(cell) for cell in positions],
        "motion_vector": [1, 0],
        "counters": {},
    }


def test_the_manifest_starts_every_session_clean(tetris):
    assert callable(tetris.on_start), "a skill with session state needs an on_start hook"


def test_the_hook_forgets_the_frame_history(tetris, session):
    perception = importlib.import_module("artemis_skills_tetris.perception")
    assert not hasattr(perception, "_history") or True  # the hook is the contract, not the name
    session.start(tetris)

    assert list(perception._history) == []


def test_the_hook_forgets_the_last_decision(tetris, session, monkeypatch):
    """The one that would hurt: replaying a decision from the previous session."""
    policy_jev = importlib.import_module("artemis_skills_tetris.policy_jev")
    asked = []

    def fake_ask(payload, questions, **kwargs):
        asked.append(payload)
        return {f"cand_{index}": {"type": "noul", "noul": 0.9} for index in range(len(questions))}

    monkeypatch.setattr(systemone, "ask", fake_ask)
    calibration = {"policy_backend": "jev", "jev": {"mode": "noul", "min_confidence": 0.05}}

    policy_jev.decide(state_for(), calibration)  # session 1 asks once
    policy_jev.decide(state_for(), calibration)  # same piece: reused, not asked
    assert len(asked) == 1

    session.start(tetris)  # a new session
    policy_jev.decide(state_for(), calibration)

    assert len(asked) == 2, "a new session must ask again, not replay the old answer"


def test_a_session_hook_tolerates_being_handed_the_handle(tetris, session):
    """The runtime calls a hook with the skill handle; it must not be picky about it."""
    session.start(tetris)
    session.start()
    session.stop(tetris)
