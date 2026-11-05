"""iOS keyboard-first guidance injected into agent system prompts.

The knowledge comes from the iPhone Settings > Accessibility > Keyboards & Typing >
Full Keyboard Access > Commands screenshots in ``captures/hot_key``.
"""

from __future__ import annotations

__all__ = ["IOS_KEYBOARD_GUIDANCE", "append_ios_shortcuts", "is_ios_device"]

_MARKER = "## iOS: keyboard before pointer"

IOS_KEYBOARD_GUIDANCE = f"""{_MARKER}

On iOS, prefer the keyboard/Full Keyboard Access over the relative mouse. Use the
pointer only when no keyboard route reaches the target. The pointer is a fallback,
not the default.

Currently executable by the ESP32 HID keyboard:

```text
Cmd+Space  Spotlight (search)
Cmd+H      Home
Space      Activate (click)
Tab        Move forward
Shift+Tab  Move backward
Up, Down, Left, Right   Move in the direction
```

Decision rules:

1. Launch an app with always use `manage_app(action="launch", app_name="...")`. The HID
   firmware types ASCII only, but Han characters are transliterated to toneless pinyin
   before sending (珍珠奶茶 -> `zhenzhunaicha`); most Chinese apps accept that in a
   search box. Other non-ASCII (emoji, kana) is still refused. Verify the result on the
   next screen — pinyin recognition differs per app.
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
"""


def _platform_value(device) -> str:
    platform = getattr(device, "mobile_platform", None)
    platform = getattr(platform, "value", platform)
    return str(platform or "").strip().lower()


def is_ios_device(ctx) -> bool:
    """True when the context's device is iOS."""
    return _platform_value(getattr(ctx, "device", None)) == "ios"


def append_ios_shortcuts(prompt: str, ctx) -> str:
    """Append keyboard-first guidance only for an iOS device (idempotent)."""
    if not is_ios_device(ctx) or _MARKER in prompt:
        return prompt
    return prompt.rstrip() + "\n\n" + IOS_KEYBOARD_GUIDANCE
