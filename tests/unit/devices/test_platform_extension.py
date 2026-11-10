"""Platform Extension DTOs/ports (ADR-010 Phase 4.3a)."""

from __future__ import annotations

import pytest

from artemis.core.capabilities import (
    CapabilityDeclaration,
    CapabilityId,
    CapabilityManifest,
)
from artemis.core.platform_extension import (
    AppLifecycleResult,
    ExtensionPortUnavailable,
    PermissionStatus,
    PlatformExtension,
    PromptGuidance,
    PromptSection,
    TextInputPlan,
)


def _manifest(*capabilities: CapabilityId) -> CapabilityManifest:
    return CapabilityManifest(
        adapter_id="ext.test",
        adapter_version="1.0",
        declarations=tuple(CapabilityDeclaration(item) for item in capabilities),
    )


class FakeAppLifecycle:
    def __init__(self):
        self.launched: list[str] = []

    def launch(self, identifier: str) -> AppLifecycleResult:
        self.launched.append(identifier)
        return AppLifecycleResult(True, f"requested {identifier}")

    def stop(self, identifier: str | None = None) -> AppLifecycleResult:
        return AppLifecycleResult(True, f"stopped {identifier or 'current'}")

    def current(self) -> str | None:
        return self.launched[-1] if self.launched else None


class FakeNavigation:
    def __init__(self):
        self.calls: list[str] = []

    def back(self) -> None:
        self.calls.append("back")

    def home(self) -> None:
        self.calls.append("home")

    def app_switch(self) -> None:
        self.calls.append("app_switch")


class FakeTextInput:
    def prepare(self, text: str) -> TextInputPlan:
        return TextInputPlan(text=text.lower(), encoding="ascii", clear_first=True)


class FakeLocator:
    def locate(self, frame, query):
        return {"query": query, "frame": frame}


def test_extension_exposes_ports_and_declared_capabilities():
    lifecycle = FakeAppLifecycle()
    navigation = FakeNavigation()
    extension = PlatformExtension(
        extension_id="IOS.Test",
        platform_family="IOS",
        capabilities=_manifest(CapabilityId.APP_LAUNCH, CapabilityId.KEYBOARD_KEY),
        app_lifecycle=lifecycle,
        navigation=navigation,
    )

    assert extension.extension_id == "ios.test"
    assert extension.platform_family == "ios"
    assert extension.supports(CapabilityId.APP_LAUNCH) is True
    assert extension.supports(CapabilityId.APP_LAUNCH) is True
    assert extension.require("navigation") is navigation


def test_a_missing_port_has_the_stable_capability_unavailable_reason():
    extension = PlatformExtension(
        extension_id="ios.test",
        platform_family="ios",
        capabilities=_manifest(CapabilityId.APP_LAUNCH),
    )

    with pytest.raises(ExtensionPortUnavailable) as excinfo:
        extension.require("grounding")

    assert excinfo.value.reason == "capability_unavailable"
    assert excinfo.value.port_name == "grounding"


def test_unknown_port_is_a_programming_error_not_a_capability_gap():
    extension = PlatformExtension(
        extension_id="ios.test",
        platform_family="ios",
        capabilities=_manifest(CapabilityId.APP_LAUNCH),
    )

    with pytest.raises(ValueError, match="unknown"):
        extension.require("telepathy")


def test_ports_are_plain_policy_objects():
    navigation = FakeNavigation()
    lifecycle = FakeAppLifecycle()
    text = FakeTextInput()

    navigation.back()
    navigation.home()
    lifecycle.launch("com.example.app")
    plan = text.prepare("Hello")

    assert navigation.calls == ["back", "home"]
    assert lifecycle.current() == "com.example.app"
    assert plan == TextInputPlan(text="hello", encoding="ascii", clear_first=True)


def test_prompt_guidance_is_non_empty_and_versioned():
    guidance = PromptGuidance(
        guidance_id="ios.keyboard",
        version="2",
        sections=(PromptSection("back", "Back", "Use the left-edge gesture."),),
    )

    assert guidance.sections[0].section_id == "back"
    with pytest.raises(ValueError, match="section"):
        PromptGuidance("ios.empty", ())


def test_permission_status_uses_the_capability_state_vocabulary():
    status = PermissionStatus("assistive_touch", "available", "pointer enabled")

    assert status.state.value == "available"
    with pytest.raises(ValueError):
        PermissionStatus("assistive_touch", "enabled")
