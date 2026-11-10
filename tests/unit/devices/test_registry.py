"""Static profile registry and pure selection (ADR-005 Phase 2a)."""

from __future__ import annotations

import asyncio

import pytest

from artemis.core.capabilities import CapabilityId
from artemis.devices.capability_adapters import (
    android_adapters,
    build_mock_profile,
    ios_adapters,
    mock_adapters,
)
from artemis.devices.composition import CompositionRequest
from artemis.devices.registry import (
    AdapterRegistry,
    DeviceProfile,
    ProfileBuildError,
    ProfileSelectionError,
    builtin_registry,
    select_device_profile,
)


def _request(**overrides):
    values = {
        "platform": "ios",
        "device_id": "ios-uvc",
        "profile_id": "ios.uvc_hid",
        "capability_ids": frozenset({CapabilityId.FRAME_CAPTURE, CapabilityId.POINTER_RELATIVE}),
    }
    values.update(overrides)
    return CompositionRequest(**values)


def test_registry_rejects_duplicate_ids():
    registry = AdapterRegistry()
    registry.register_adapter(android_adapters()[0])
    with pytest.raises(ValueError, match="duplicate adapter_id"):
        registry.register_adapter(android_adapters()[0])

    profile = DeviceProfile("p", "android", adapter_ids=("android.app",))
    registry.register_profile(profile)
    with pytest.raises(ValueError, match="duplicate profile_id"):
        registry.register_profile(profile)


def test_an_explicit_profile_is_selected_when_capabilities_match():
    registry = builtin_registry()
    selection = select_device_profile(_request(), registry)

    assert selection.profile_id == "ios.uvc_hid"
    assert selection.reason == "explicit profile"
    assert selection.profile.platform_family == "ios"


def test_platform_mismatch_and_missing_capability_are_structured():
    registry = builtin_registry()

    with pytest.raises(ProfileSelectionError) as mismatch:
        select_device_profile(_request(platform="android"), registry)
    assert mismatch.value.reason == "platform_mismatch"

    with pytest.raises(ProfileSelectionError) as missing:
        select_device_profile(
            _request(capability_ids=frozenset({CapabilityId.FRAME_CAPTURE})),
            registry,
        )
    assert missing.value.reason == "missing_capability"
    assert CapabilityId.POINTER_RELATIVE in missing.value.missing_capabilities


def test_an_unregistered_adapter_is_reported():
    registry = AdapterRegistry()
    registry.register_profile(DeviceProfile("weird", "android", adapter_ids=("not.registered",)))

    with pytest.raises(ProfileSelectionError) as excinfo:
        select_device_profile(
            CompositionRequest(platform="android", device_id="x"),
            registry,
        )
    assert excinfo.value.reason == "missing_profile"


def test_selection_is_deterministic_when_several_profiles_match():
    registry = builtin_registry()
    first = select_device_profile(
        CompositionRequest(
            platform="android",
            device_id="a",
            capability_ids=frozenset({CapabilityId.UI_TREE_READ, CapabilityId.TOUCH_ABSOLUTE}),
        ),
        registry,
    )
    second = select_device_profile(
        CompositionRequest(
            platform="android",
            device_id="a",
            capability_ids=frozenset({CapabilityId.UI_TREE_READ, CapabilityId.TOUCH_ABSOLUTE}),
        ),
        registry,
    )
    assert first.profile_id == second.profile_id == "android.app"


def test_the_mock_profile_builds_a_session_without_hardware():
    registry = builtin_registry()
    request = CompositionRequest(
        platform="mock",
        device_id="mock-device",
        screen_size=(1080, 2400),
        capability_ids=frozenset({CapabilityId.FRAME_CAPTURE}),
    )
    selection = select_device_profile(request, registry)
    build = selection.profile.build(request, registry)

    assert build.profile_id == "mock.screen"
    assert build.session is not None
    assert build.driver is not None
    asyncio.run(build.session.close())


def test_a_profile_without_a_builder_fails_explicitly():
    registry = AdapterRegistry()
    profile = DeviceProfile("declared.only", "ios", adapter_ids=())
    registry.register_profile(profile)

    assert profile.build is None
    with pytest.raises(ProfileBuildError, match="builder"):
        if profile.build is None:
            raise ProfileBuildError(
                "builder_not_registered", "no builder", profile_id=profile.profile_id
            )


def test_builtin_registry_contains_the_three_reference_profiles():
    registry = builtin_registry()
    assert {profile.profile_id for profile in registry.profiles} == {
        "android.app",
        "ios.uvc_hid",
        "mock.screen",
    }
    assert len(registry.adapters) == 5
