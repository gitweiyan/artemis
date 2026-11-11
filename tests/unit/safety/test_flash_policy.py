"""Declared Flash risk posture (Stage 4)."""

from artemis.safety.flash import FlashMode, flash_action_policy


def test_single_pointer_actions_require_precondition_but_post_verification_is_opt_in():
    policy = flash_action_policy("click")

    assert policy.mode is FlashMode.SINGLE
    assert policy.requires_precondition is True
    assert policy.requires_post_verification is False
    assert policy.pointer_precheck is True
    assert policy.burst_allowed is False


def test_burst_disables_precondition_and_keeps_post_verification_opt_in():
    policy = flash_action_policy("click", burst=True)

    assert policy.mode is FlashMode.BURST
    assert policy.requires_precondition is False
    assert policy.requires_post_verification is False
    assert policy.burst_allowed is False


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
