"""iOS keyboard-first guidance injected into agent system prompts.

The knowledge comes from the iPhone Settings > Accessibility > Keyboards & Typing >
Full Keyboard Access > Commands screenshots in ``captures/hot_key``.
"""

from __future__ import annotations

from artemis.core.platform_extension import extension_from_context

__all__ = ["IOS_KEYBOARD_GUIDANCE", "append_ios_shortcuts", "is_ios_device"]

_MARKER = "## iOS: keyboard before pointer"

IOS_KEYBOARD_GUIDANCE = f"""{_MARKER}

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


def _platform_value(device) -> str:
    platform = getattr(device, "mobile_platform", None)
    platform = getattr(platform, "value", platform)
    return str(platform or "").strip().lower()


def is_ios_device(ctx) -> bool:
    """True when the context's device is iOS."""
    return _platform_value(getattr(ctx, "device", None)) == "ios"


def append_ios_shortcuts(prompt: str, ctx) -> str:
    """Append Profile-provided guidance (legacy platform fallback) once, idempotently."""
    extension = extension_from_context(ctx)
    guidance = getattr(extension, "prompt_guidance", None) if extension is not None else None
    if guidance is not None:
        marker = f"<!-- platform-extension:{guidance.guidance_id} -->"
        if marker in prompt:
            return prompt
        rendered = "\n\n".join(
            f"## {section.title}\n{section.body}" for section in guidance.sections
        )
        return prompt.rstrip() + "\n\n" + marker + "\n\n" + rendered
    if not is_ios_device(ctx) or _MARKER in prompt:
        return prompt
    return prompt.rstrip() + "\n\n" + IOS_KEYBOARD_GUIDANCE
