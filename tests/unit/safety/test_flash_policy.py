"""Declared Flash risk posture (Stage 4)."""

from artemis.safety.flash import FlashMode, flash_action_policy


def test_single_pointer_actions_require_precondition_but_post_verification_is_opt_in():
    policy = flash_action_policy("click")

    assert policy.mode is FlashMode.SINGLE
    assert policy.requires_precondition is True
    assert policy.requires_post_verification is False
    assert policy.pointer_precheck is True
    assert policy.burst_allowed is True


def test_burst_disables_precondition_and_keeps_post_verification_opt_in():
    policy = flash_action_policy("click", burst=True)

    assert policy.mode is FlashMode.BURST
    assert policy.requires_precondition is False
    assert policy.requires_post_verification is False
    assert policy.burst_allowed is True


def test_launch_does_not_pay_for_post_verification_by_default():
    policy = flash_action_policy("manage_app")

    assert policy.requires_precondition is False
    assert policy.requires_post_verification is False
    assert policy.pointer_precheck is False
    assert policy.burst_allowed is True


def _png(color):
    from io import BytesIO
    from PIL import Image

    out = BytesIO()
    Image.new("RGB", (100, 100), color).save(out, "PNG")
    return out.getvalue()


def test_high_value_flash_post_verification_marks_no_effect():
    from types import SimpleNamespace
    from artemis.agents.flash.runner import FlashRunner

    runner = object.__new__(FlashRunner)
    runner.ctx = SimpleNamespace(device=SimpleNamespace(device_width=1000, device_height=1000))
    result = SimpleNamespace(
        status="success",
        text_summary="Requested launch",
        metadata={
            "_pre_screenshot_bytes": _png((0, 0, 0)),
            "_post_screenshot_bytes": _png((0, 0, 0)),
        },
    )

    FlashRunner._flash_post_verification(runner, "manage_app", {"verify_after": True}, result)

    assert result.status == "error"
    assert result.metadata["post_verification"]["ok"] is False
    assert result.metadata["post_verification"]["incident"] == "no_effect"


def test_flash_precondition_checks_frame_target_shape_and_index():
    from types import SimpleNamespace
    from artemis.safety.flash import evaluate_flash_precondition

    assert evaluate_flash_precondition(
        "click", {"target": [500, 500]}, pre_screenshot=b"frame", ctx=None
    ).allowed
    missing = evaluate_flash_precondition(
        "click", {"target": [500, 500]}, pre_screenshot=None, ctx=None
    )
    assert missing.incident == "stale_frame"
    outside = evaluate_flash_precondition(
        "click", {"target": [1200, 500]}, pre_screenshot=b"frame", ctx=None
    )
    assert outside.incident == "coordinate_space_mismatch"
    state = SimpleNamespace(indexed_elements=[{"center": [1, 2]}])
    missing_index = evaluate_flash_precondition(
        "click", {"target": 2}, state=state, pre_screenshot=b"frame", ctx=None
    )
    assert missing_index.incident == "target_disappeared"


def test_flash_pointer_precheck_requires_a_relative_sink_and_delegates_alignment():
    from types import SimpleNamespace
    from artemis.safety.flash import evaluate_flash_precondition

    class Control:
        capabilities = SimpleNamespace(supports_relative_pointer=True)

    session = SimpleNamespace(
        capabilities=SimpleNamespace(requires_pointer_precheck=True),
        control=[Control()],
    )
    ctx = SimpleNamespace(device_session=session)
    decision = evaluate_flash_precondition(
        "click", {"target": [500, 500]}, pre_screenshot=b"frame", ctx=ctx
    )
    assert decision.allowed is True
    assert decision.pointer_precheck == "delegated"

    session.control = []
    missing = evaluate_flash_precondition(
        "click", {"target": [500, 500]}, pre_screenshot=b"frame", ctx=ctx
    )
    assert missing.allowed is False
    assert missing.incident == "capability_unavailable"


def test_flash_burst_budget_caps_actions_and_disallows_unknown_actions():
    from artemis.safety.flash import flash_burst_budget

    budget = flash_burst_budget()

    assert budget.allows("click", 1) is True
    assert budget.allows("click", budget.max_actions) is True
    assert budget.allows("click", budget.max_actions + 1) is False
    assert budget.allows("unknown_action", 1) is False


def test_flash_rejects_a_stale_high_risk_observation(monkeypatch):
    from types import SimpleNamespace
    from artemis.runtime import observation_clock
    from artemis.safety.flash import FLASH_MAX_OBSERVATION_AGE_S, evaluate_flash_precondition

    monkeypatch.setattr("artemis.runtime.observation_clock.time.monotonic", lambda: 100.0)
    ctx = SimpleNamespace()
    observation_clock.stamp_capture(ctx, now=100.0 - FLASH_MAX_OBSERVATION_AGE_S - 1.0)

    stale = evaluate_flash_precondition(
        "click", {"target": [500, 500]}, pre_screenshot=b"frame", ctx=ctx
    )
    assert stale.allowed is False
    assert stale.incident == "stale_frame"
    assert stale.observation_skew_s == FLASH_MAX_OBSERVATION_AGE_S + 1.0
    assert stale.max_observation_age_s == FLASH_MAX_OBSERVATION_AGE_S

    observation_clock.stamp_capture(ctx, now=100.0 - FLASH_MAX_OBSERVATION_AGE_S + 0.5)
    fresh = evaluate_flash_precondition(
        "click", {"target": [500, 500]}, pre_screenshot=b"frame", ctx=ctx
    )
    assert fresh.allowed is True
    assert fresh.max_observation_age_s == FLASH_MAX_OBSERVATION_AGE_S


def test_flash_does_not_apply_the_high_risk_age_cap_to_a_key_action():
    from types import SimpleNamespace
    from artemis.runtime import observation_clock
    from artemis.safety.flash import evaluate_flash_precondition

    ctx = SimpleNamespace()
    observation_clock.stamp_capture(ctx, now=0.0)
    decision = evaluate_flash_precondition(
        "press_key", {"key": "HOME"}, pre_screenshot=b"frame", ctx=ctx
    )
    assert decision.allowed is True
    assert decision.max_observation_age_s is None
