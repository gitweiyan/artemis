"""iOS Platform Extension adapters (ADR-010 Phase 4.3b)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from artemis.core.capabilities import CapabilityId
from artemis.core.platform_extension import (
    AppLifecycleResult,
    ExtensionPortUnavailable,
    TextInputPlan,
)
from artemis.drivers.ios.extension import (
    IOSAppLifecycle,
    IOSNavigation,
    IOSTargetAdjuster,
    IOSTextInput,
    build_ios_extension,
)


class FakeControl:
    def __init__(self):
        self.calls: list[str] = []

    def ios_home(self):
        self.calls.append("home")

    def press_named_key(self, name):
        self.calls.append(name)


class FakeLauncher:
    def __init__(self):
        self.calls: list[tuple[str, object]] = []

    def open_app(self, app, *, capture=None):
        self.calls.append((app, capture))
        return SimpleNamespace(ok=True, app=app, query=app, method="spotlight", error=None)


class FakeSink:
    def __init__(self):
        self.calls = 0

    def go_back(self):
        self.calls += 1
        return "gesture"


def test_app_lifecycle_returns_a_semantic_result_not_a_ui_claim():
    control = FakeControl()
    launcher = FakeLauncher()
    lifecycle = IOSAppLifecycle(control, launcher=launcher, capture=object())

    result = lifecycle.launch("设置")

    assert result.accepted is True
    assert result.needs_observation is True
    assert launcher.calls == [("设置", lifecycle.capture)]
    assert lifecycle.stop("设置").accepted is False


def test_navigation_maps_home_and_back_to_the_ios_gesture():
    control = FakeControl()
    sink = FakeSink()
    navigation = IOSNavigation(control, go_back=sink.go_back)

    assert navigation.back() == "gesture"
    navigation.home()
    navigation.app_switch()

    assert sink.calls == 1
    assert control.calls == ["home", "app_switch"]


def test_navigation_refuses_back_without_the_gesture_sink():
    navigation = IOSNavigation(FakeControl())

    with pytest.raises(RuntimeError, match="left-edge"):
        navigation.back()


def test_text_preparation_transliterates_han_and_rejects_other_non_ascii():
    policy = IOSTextInput()

    assert policy.prepare("设置") == TextInputPlan("shezhi", "ascii", True)
    with pytest.raises(ValueError, match="ASCII"):
        policy.prepare("emoji 😀")


def test_target_adjuster_is_a_callable_uvc_source_plugin():
    adjuster = IOSTargetAdjuster()

    assert adjuster(b"not an image", (0.1, 0.1, 0.2, 0.2)) is None
    assert adjuster.adjust(SimpleNamespace(box=(0.1, 0.1, 0.2, 0.2)), frame=b"bad") is None


def test_built_extension_composes_the_ios_ports():
    extension = build_ios_extension(FakeControl(), sink=FakeSink())

    assert extension.platform_family == "ios"
    assert extension.supports(CapabilityId.APP_LAUNCH)
    assert extension.supports(CapabilityId.KEYBOARD_KEY)
    assert extension.supports(CapabilityId.TEXT_INPUT)
    assert extension.app_lifecycle is not None
    assert extension.navigation is not None
    assert extension.text_input is not None
    assert extension.target_adjuster is not None
    assert extension.prompt_guidance is not None
    assert extension.prompt_guidance.sections[0].section_id == "ios.keyboard"

    with pytest.raises(ExtensionPortUnavailable):
        extension.require("grounding")


def test_lifecycle_returns_failure_as_data():
    class FailingLauncher:
        def open_app(self, app, *, capture=None):
            return SimpleNamespace(ok=False, app=app, query=app, method="spotlight", error="no_screen_change")

    result = IOSAppLifecycle(FakeControl(), launcher=FailingLauncher()).launch("Settings")

    assert result == AppLifecycleResult(False, "no_screen_change", needs_observation=True)
