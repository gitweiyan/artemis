"""T1: the reference frames are the calibration corpus — real device pixels.

A vision skill's tests need real frames: synthetic images prove the algorithm, the
game's own pixels prove the *calibration*. These three frames come from the live
observation run (2026-10-12) and are the ground truth every later task asserts on.
"""

from __future__ import annotations

import importlib

import pytest

from artemis.reflex.registry import load_skill


@pytest.fixture(scope="module")
def frames_module():
    load_skill("tetris")  # registers artemis_skills_tetris
    return importlib.import_module("artemis_skills_tetris.frames")


def test_the_corpus_holds_three_real_screenshots(frames_module):
    frames = frames_module.load_reference_frames()

    assert len(frames) >= 3, "the calibration corpus is missing its reference frames"
    for name, data, size in frames:
        assert size == (720, 1600), f"{name} is {size}, not a PEMM00 screenshot"
        assert data.startswith(b"\xff\xd8\xff") or data.startswith(b"\x89PNG"), name


def test_frames_come_back_in_a_stable_order(frames_module):
    names = [name for name, _, _ in frames_module.load_reference_frames()]

    assert names == sorted(names)
    assert len(set(names)) == len(names)
