"""2026-11-05 retro I4 / batch item 5: Flash has no no-op guard.

The 15:37 milk-tea run pressed BACK four times with zero screen change and the
loop never pushed back. ``incident``/``crop_change`` and the pre/post dHash do
exist in the driver and in ``data_engine``, but the model only ever sees
``text_summary``, so nothing carries that evidence into the next prompt.

Replaying that run's 49 real dHash pairs shows the no-ops were *scattered*: the
longest cluster was two (steps 5-6 and 39-40), so this guard would have nudged
the model twice but would not have aborted that particular run. It is a bounded
stop for a genuinely stalled loop, not a fix that rescues 2026-11-05.

These tests pin a bounded cross-step guard: two consecutive no-ops ask for a
strategy change, three stop the run, a visible change resets the counter, and
legitimate waiting is neither counted nor allowed to clear a stagnation.
"""

from artemis.agents.flash.no_op import WAIT_ACTIONS, NoOpTracker


def _observe(
    tracker,
    *,
    action="click",
    status="success",
    incident=None,
    metrics=None,
    unchanged=None,
):
    return tracker.observe(
        action,
        status=status,
        incident=incident,
        metrics=metrics,
        unchanged=unchanged,
    )


def test_the_guard_carries_the_dhash_change_for_effect_evidence():
    tracker = NoOpTracker()

    changed = _observe(tracker, unchanged=False)
    unchanged = _observe(tracker, incident="no_effect", unchanged=True)

    assert changed.changed is True
    assert changed.is_no_op is False
    assert unchanged.changed is False
    assert unchanged.is_no_op is True


def test_a_single_no_effect_does_not_nag_the_model():
    tracker = NoOpTracker()

    verdict = _observe(tracker, incident="no_effect", unchanged=True)

    assert verdict.is_no_op is True
    assert verdict.consecutive == 1
    assert verdict.action == "none"
    assert verdict.notice is None


def test_two_consecutive_no_ops_ask_for_a_strategy_change():
    tracker = NoOpTracker()
    _observe(tracker, incident="no_effect", unchanged=True)

    verdict = _observe(tracker, status="failed", unchanged=True)

    assert verdict.consecutive == 2
    assert verdict.action == "warn"
    assert "switch" in verdict.notice.lower()


def test_three_consecutive_no_ops_abort_the_run():
    tracker = NoOpTracker()
    _observe(tracker, incident="no_effect", unchanged=True)
    _observe(tracker, incident="no_effect", unchanged=True)

    verdict = _observe(tracker, incident="no_effect", unchanged=True)

    assert verdict.consecutive == 3
    assert verdict.action == "abort"
    assert verdict.notice


def test_a_stalled_loop_of_silent_backs_stops_on_the_third_turn():
    """status=success + identical frames was exactly the silent-BACK failure.

    Hypothetical, not a replay of 15:37 (there the BACKs were separated by real
    screen changes). It pins the bounded stop so a stall cannot run forever.
    """
    tracker = NoOpTracker()

    actions = [_observe(tracker, action="press_key", unchanged=True).action for _ in range(4)]

    assert actions == ["none", "warn", "abort", "abort"]


def test_a_visible_change_resets_the_counter():
    tracker = NoOpTracker()
    _observe(tracker, incident="no_effect", unchanged=True)

    verdict = _observe(tracker, unchanged=False)

    assert verdict.is_no_op is False
    assert verdict.consecutive == 0
    assert tracker.consecutive == 0


def test_waiting_is_legitimate_stillness_not_a_no_op():
    tracker = NoOpTracker()
    _observe(tracker, incident="no_effect", unchanged=True)
    assert "wait" in WAIT_ACTIONS

    verdict = _observe(tracker, action="wait", unchanged=True)

    assert verdict.is_no_op is False
    assert verdict.consecutive == 1, "a wait must not clear an existing stagnation"
    assert _observe(tracker, incident="no_effect", unchanged=True).consecutive == 2


def test_a_sheet_opening_away_from_the_target_is_not_a_no_op():
    """crop_change is a target-region signal; a changed frame vetoes it."""
    tracker = NoOpTracker()

    verdict = _observe(tracker, metrics={"crop_change": 0.0}, unchanged=False)

    assert verdict.is_no_op is False


def test_low_crop_change_counts_when_the_frame_hash_is_unavailable():
    tracker = NoOpTracker()

    verdict = _observe(tracker, metrics={"crop_change": 0.0092}, unchanged=None)

    assert verdict.is_no_op is True
    assert verdict.reason == "crop_unchanged"


def test_a_high_crop_change_is_a_real_reaction():
    tracker = NoOpTracker()

    verdict = _observe(tracker, metrics={"crop_change": 0.53}, unchanged=None)

    assert verdict.is_no_op is False
