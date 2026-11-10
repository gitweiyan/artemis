"""iOS Platform Extension: semantic policies around the UVC/HID adapters (ADR-010).

The extension owns iOS-specific *policy* (Spotlight launch, left-edge BACK,
pinyin text, SpringBoard icon lift, prompt guidance). Generic perception/action
code only sees the ports defined in :mod:`artemis.core.platform_extension`.

This is the production adapter behind those ports; profile wiring is additive and
does not change the legacy `is_ios_device` path until Phase 4.3d.
"""

from __future__ import annotations

from typing import Any, Callable

from artemis.core.capabilities import (
    CapabilityDeclaration,
    CapabilityId,
    CapabilityManifest,
)
from artemis.core.platform_extension import (
    AppLifecycleResult,
    PlatformExtension,
    PromptGuidance,
    PromptSection,
    TextInputPlan,
)
from artemis.perception.ios_targets import icon_above_label
from artemis.reflex.ios_apps import IOSAppLauncher
from artemis.reflex.pinyin import to_ascii_input

__all__ = [
    "IOSAppLifecycle",
    "IOSNavigation",
    "IOSPlatformExtension",
    "IOSTargetAdjuster",
    "IOSTextInput",
    "build_ios_extension",
]

_IOS_PROMPT_SECTION = PromptSection(
    "ios.keyboard",
    "iOS keyboard before pointer",
    "Prefer keyboard/Full Keyboard Access over the relative pointer. BACK is a "
    "left-edge swipe, not a key; launch apps through manage_app/Spotlight and verify "
    "the resulting screen.",
)


class IOSAppLifecycle:
    def __init__(self, control, *, capture=None, launcher=None) -> None:
        self.control = control
        self.capture = capture
        self.launcher = launcher or IOSAppLauncher(control)

    def launch(self, identifier: str) -> AppLifecycleResult:
        result = self.launcher.open_app(identifier, capture=self.capture)
        detail = result.error or f"{result.method} {result.query!r}"
        return AppLifecycleResult(result.ok, detail, needs_observation=True)

    def stop(self, identifier: str | None = None) -> AppLifecycleResult:
        return AppLifecycleResult(
            False,
            "iOS has no public stop/force-quit HID path; use Home and verify",
            needs_observation=False,
        )

    def current(self) -> str | None:
        return None


class IOSNavigation:
    def __init__(
        self,
        control,
        *,
        go_back: Callable[[], Any] | None = None,
    ) -> None:
        self.control = control
        self._go_back = go_back

    def back(self) -> Any:
        if self._go_back is None:
            raise RuntimeError("iOS BACK requires a sink with the left-edge go_back gesture")
        return self._go_back()  # ActionResult or an awaitable, owned by the caller

    def home(self) -> Any:
        return self.control.ios_home()

    def app_switch(self) -> Any:
        return self.control.press_named_key("app_switch")


class IOSTextInput:
    """ASCII/pinyin preparation; the firmware's ``type`` command is ASCII-only."""

    def prepare(self, text: str) -> TextInputPlan:
        prepared = to_ascii_input(str(text))
        if not prepared.isascii():
            raise ValueError(f"iOS HID text is ASCII-only: {prepared!r}")
        return TextInputPlan(prepared, encoding="ascii", clear_first=True)


class IOSTargetAdjuster:
    """Lift a SpringBoard label target onto the icon above it."""

    def __call__(self, image: bytes, box: tuple[float, float, float, float]):
        return icon_above_label(image, box)

    def adjust(self, target: Any, *, frame: Any | None = None) -> Any | None:
        image = getattr(frame, "image", frame)
        if not isinstance(image, (bytes, bytearray)):
            return None
        return icon_above_label(bytes(image), target.box)


class IOSPlatformExtension(PlatformExtension):
    """The iOS profile's extension, with its capability manifest."""

    @classmethod
    def build(cls, control, *, sink=None, capture=None) -> IOSPlatformExtension:
        capabilities = CapabilityManifest(
            adapter_id="ios.extension",
            adapter_version="1",
            declarations=(
                CapabilityDeclaration(CapabilityId.APP_LAUNCH),
                CapabilityDeclaration(CapabilityId.KEYBOARD_KEY),
                CapabilityDeclaration(CapabilityId.TEXT_INPUT),
            ),
        )
        return cls(
            extension_id="ios.keyboard_pointer",
            platform_family="ios",
            capabilities=capabilities,
            app_lifecycle=IOSAppLifecycle(control, capture=capture),
            navigation=IOSNavigation(
                control,
                go_back=getattr(sink, "go_back", None) if sink is not None else None,
            ),
            text_input=IOSTextInput(),
            target_adjuster=IOSTargetAdjuster(),
            prompt_guidance=PromptGuidance(
                guidance_id="ios.keyboard",
                sections=(_IOS_PROMPT_SECTION,),
            ),
        )


def build_ios_extension(control, *, sink=None, capture=None) -> IOSPlatformExtension:
    return IOSPlatformExtension.build(control, sink=sink, capture=capture)
