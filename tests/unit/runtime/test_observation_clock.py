"""I5 · the observation→dispatch skew (stale-observation metric).

Why this exists: on 2026-10-09 the operator's own turns took 3–9 s, so every
action was dispatched against a screen captured seconds earlier. The one failure
that race produced could only be classified by hand from log timestamps
(2.4–8.5 s), and the retrospective's own root cause stayed speculative because
nothing recorded the interval.

Design note: the capture stamp and the skew live on the run context, next to
``ctx.transcript_ledger`` / ``ctx.ui_adb_client``, not on ``State`` — the state
contract and its per-node visibility manifest stay untouched.
"""

from __future__ import annotations

import importlib
import logging
from types import SimpleNamespace


def _module():
    """The clock helpers, failing cleanly while they do not exist yet."""
    try:
        module = importlib.import_module("artemis.runtime.observation_clock")
    except ImportError as exc:
        raise AssertionError(f"the observation clock is missing: {exc}") from exc
    assert hasattr(module, "stamp_capture"), "the observation clock is missing"
    return module


oc = None  # bound by the fixture-free helper below


def test_skew_is_the_interval_between_capture_and_dispatch():
    ctx = SimpleNamespace()

    _module().stamp_capture(ctx, now=100.0)

    assert _module().skew_s(ctx, now=104.5) == 4.5


def test_without_a_capture_stamp_there_is_no_verdict():
    """A turn with no observation of its own must not invent a number."""
    ctx = SimpleNamespace()

    assert _module().skew_s(ctx, now=10.0) is None


def test_dispatch_notes_the_skew_and_warns_when_the_observation_is_stale(caplog):
    ctx = SimpleNamespace()
    _module().stamp_capture(ctx, now=0.0)

    with caplog.at_level(logging.WARNING):
        skew = _module().note_dispatch_skew(ctx, now=9.0)

    assert skew == 9.0
    assert ctx.observation_skew_s == 9.0
    assert "stale observation" in caplog.text.lower()


def test_dispatch_stays_quiet_when_the_observation_is_fresh(caplog):
    ctx = SimpleNamespace()
    _module().stamp_capture(ctx, now=0.0)

    with caplog.at_level(logging.WARNING):
        skew = _module().note_dispatch_skew(ctx, now=1.5)

    assert skew == 1.5
    assert ctx.observation_skew_s == 1.5
    assert not caplog.records, "a fresh observation must not warn"


def test_the_warning_threshold_is_five_seconds_and_overridable_per_context(caplog):
    assert _module().STALE_OBSERVATION_WARN_SECONDS == 5.0

    ctx = SimpleNamespace(stale_observation_warn_s=1.0)
    _module().stamp_capture(ctx, now=0.0)
    with caplog.at_level(logging.WARNING):
        _module().note_dispatch_skew(ctx, now=2.0)

    assert "stale observation" in caplog.text.lower()


def test_a_second_capture_replaces_the_first():
    """Each turn's action belongs to the newest observation, not an older one."""
    ctx = SimpleNamespace()
    _module().stamp_capture(ctx, now=100.0)
    _module().stamp_capture(ctx, now=120.0)

    assert _module().skew_s(ctx, now=121.0) == 1.0


def test_skew_metadata_is_measured_now_not_reused_from_the_last_dispatch():
    """The step record must carry *its own* interval.

    The turn record is written before its action is dispatched, so a value stored
    at dispatch time belongs to the previous turn: reading it here would shift
    every number by one step (observed on hardware: step 1 empty, step 2 carrying
    step 1's 6.73 s).
    """
    ctx = SimpleNamespace()
    assert _module().skew_metadata(ctx, now=10.0) == {}

    _module().stamp_capture(ctx, now=100.0)
    ctx.observation_skew_s = 99.0  # a stale dispatch-time value must not leak in

    assert _module().skew_metadata(ctx, now=103.5) == {"observation_skew_s": 3.5}
