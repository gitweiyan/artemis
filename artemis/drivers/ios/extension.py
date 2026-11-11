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
from artemis.drivers.ios.apps import IOSAppLauncher
from artemis.text.pinyin import to_ascii_input

__all__ = [
    "IOSAppLifecycle",
    "IOSNavigation",
    "IOSPlatformExtension",
    "IOSTargetAdjuster",
    "IOSTextInput",
    "build_ios_extension",
]

# Transitional copy of the legacy guidance data, so the Profile path keeps the full
# keyboard rules while `agents/ios_shortcuts.py` remains the Phase 9-A fallback.
_IOS_PROMPT_BODY = """## iOS: keyboard before pointer

On iOS, prefer the keyboard/Full Keyboard Access over the relative mouse. Use the
pointer only when no keyboard route reaches the target. The pointer is a fallback,
not the default.

Currently executable by the ESP32 HID keyboard:

```text
Cmd+Space  Spotlight (in-app search fields only — launch apps with manage_app)
Cmd+H      Home
Space      Activate (click)
Tab        Move forward
Shift+Tab  Move backward
Up, Down, Left, Right   Move in the direction
```

BACK is the exception: there is no Back key. `press_key("BACK")` runs the iOS
interactive pop gesture — a rightward drag that starts on the **left edge** of the
screen (`go_back` in `artemis/control/hid.py`). Use it to leave a pushed page before
hunting for an on-screen back arrow. If the drag fails, fall back to `escape`, the
on-screen arrow, or `home` (Cmd+H). A successful BACK is reported as a left-edge
swipe, never as "Pressed key".

Decision rules:

1. Launch or reset an app **only** with `manage_app(action="launch", app_name="...")`.
   Do not recreate its sequence yourself (`Cmd+Space` -> type -> Enter): that route is
   how a launch silently ends on the home screen while reporting success. `manage_app`
   clears the stale query, opens the top result, dismisses a leftover search overlay
   and reports the screen it reached. If it reports a failure, read the screen its
   message names, then use the on-screen icon or Siri suggestion. Footnote on the name:
   the HID firmware types ASCII only, and Han characters are transliterated to toneless
   pinyin before sending (珍珠奶茶 -> `zhenzhunaicha`); other non-ASCII (emoji, kana) is
   still refused. Verify the result on the next screen — pinyin recognition differs per
   app.
2. Navigate and activate with Tab/arrows/Space before touching the pointer.
3. If a keyboard command is unsupported, say so and fall back to the pointer.
4. Never claim a keyboard launch worked without checking the resulting screen.
5. A focused search/text field consumes arrow keys: they edit suggestions, not
   Full Keyboard Access focus. Press Escape/Tab or leave the field before using
   arrows for UI focus; verify the focus change on screen.
6. Spotlight can open with Cmd+Space but Escape may not close it on the first
   press. Check the frame before assuming the overlay is gone.
7. Before retyping into a field that may already contain text, call `clear_text`
   (Cmd+A + Backspace). Appending to stale text produces unsearchable strings
   such as `Mai2enzhunaichaha`.
8. BACK is a left-edge gesture, not a keyboard command. Do not report it as
   unsupported, and do not silently repeat it when the screen does not change.
"""

_IOS_PROMPT_SECTION = PromptSection(
    "ios.keyboard",
    "iOS: keyboard before pointer",
    _IOS_PROMPT_BODY,
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
    def build(cls, control, *, sink=None, capture=None, locator=None) -> IOSPlatformExtension:
        capabilities = CapabilityManifest(
            adapter_id="ios.extension",
            adapter_version="1",
            declarations=(
                CapabilityDeclaration(CapabilityId.APP_LAUNCH),
                CapabilityDeclaration(CapabilityId.KEYBOARD_KEY),
                CapabilityDeclaration(CapabilityId.TEXT_INPUT),
                *(
                    (CapabilityDeclaration(CapabilityId.GROUNDING_LOCATE),)
                    if locator is not None
                    else ()
                ),
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
            grounding=locator,
            prompt_guidance=PromptGuidance(
                guidance_id="ios.keyboard",
                sections=(_IOS_PROMPT_SECTION,),
            ),
        )


def build_ios_extension(control, *, sink=None, capture=None, locator=None) -> IOSPlatformExtension:
    return IOSPlatformExtension.build(control, sink=sink, capture=capture, locator=locator)
