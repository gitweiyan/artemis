"""Declared Flash risk posture (Stage 4)."""

from artemis.safety.flash import FlashMode, flash_action_policy


def test_single_pointer_actions_require_precondition_and_post_verification():
    policy = flash_action_policy("click")

    assert policy.mode is FlashMode.SINGLE
    assert policy.requires_precondition is True
    assert policy.requires_post_verification is True
    assert policy.pointer_precheck is True
    assert policy.burst_allowed is False


def test_burst_disables_precondition_but_keeps_declared_post_verification():
    policy = flash_action_policy("click", burst=True)

    assert policy.mode is FlashMode.BURST
    assert policy.requires_precondition is False
    assert policy.requires_post_verification is True
    assert policy.burst_allowed is False


def test_launch_requires_post_verification_but_no_pointer_precheck():
    policy = flash_action_policy("manage_app")

    assert policy.requires_precondition is False
    assert policy.requires_post_verification is True
    assert policy.pointer_precheck is False
    assert policy.burst_allowed is True
