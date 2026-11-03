"""Phased pointer motion: the policy and the per-phase calibration, no I/O."""

from __future__ import annotations

import pytest

from artemis.reflex.motion import MotionCalibration, MotionConfig, Phase, choose_phase


def test_phase_boundaries_are_multiples_of_the_target_width():
    cfg = MotionConfig(target_width_px=100.0)
    assert choose_phase(500, cfg) is Phase.BALLISTIC  # > 4W
    assert choose_phase(400, cfg) is Phase.APPROACH  # 4W is not ballistic
    assert choose_phase(150, cfg) is Phase.APPROACH  # 1.5W is not fine
    assert choose_phase(149, cfg) is Phase.FINE


def test_units_convert_through_the_phase_gain():
    cal = MotionCalibration((2.0, 2.0))
    assert cal.units(Phase.BALLISTIC, 100, -50) == (50, -25)


def test_update_retunes_only_the_phase_that_moved():
    cal = MotionCalibration((2.0, 2.0), ema=0.5)
    cal.update(Phase.FINE, (10, 0), (20.0, 0.0))  # measured 2.0 -> unchanged
    cal.update(Phase.FINE, (10, 0), (40.0, 0.0))  # measured 4.0 -> 3.0 after EMA
    assert cal.gain(Phase.FINE) == pytest.approx((3.0, 2.0))
    assert cal.gain(Phase.BALLISTIC) == pytest.approx((2.0, 2.0))


def test_zero_unit_axes_and_implausible_ratios_leave_the_gain_alone():
    cal = MotionCalibration((2.0, 2.0), ema=1.0)
    cal.update(Phase.FINE, (0, 10), (0.0, 1.0))  # zero-unit axis: no ratio to learn
    assert cal.gain(Phase.FINE)[0] == pytest.approx(2.0)
    for _ in range(50):
        cal.update(Phase.FINE, (10, 10), (10_000.0, 10_000.0))  # a mis-detection, not a gain
    assert cal.gain(Phase.FINE) == pytest.approx((2.0, 2.0))


def test_the_fine_ratio_does_not_need_seven_observations():
    """At ~0.9 s per observation the ratios must close a residual in 2-3 segments."""
    from artemis.reflex.motion import MotionConfig, Phase

    cfg = MotionConfig()
    assert cfg.ratios[Phase.BALLISTIC] == pytest.approx(0.90)
    assert cfg.ratios[Phase.APPROACH] == pytest.approx(0.80)
    assert cfg.ratios[Phase.FINE] == pytest.approx(0.80)
    assert cfg.min_units_for_calibration == 4
