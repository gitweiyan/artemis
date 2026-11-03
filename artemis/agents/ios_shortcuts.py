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
Cmd+Tab    App Switcher
Cmd+W      close sheet/window
Esc, Tab, Up, Down, Left, Right, Space, Enter
```

Full Keyboard Access commands (Settings > Accessibility > Keyboards & Typing >
Full Keyboard Access > Commands). The Globe key is shown as "->" in the screenshots;
use these when the keyboard layer can send Globe/Fn:

```text
Help                 Globe+H
Move forward/back    Globe+Right / Globe+Shift+Right
Move up/down/left    Arrow keys
Move to beginning    Globe+Left
Move to end          Globe+Right
Next item            Control+Right
Previous item        Control+Shift+Right
Find                 Globe+F
Activate             Space
Back                 Globe+B
Context menu         Globe+M
Actions              Globe+Z
Home                 Fn+H
App Switcher         Fn+Up
Control Center       Fn+C
Notification Center  Fn+N
Lock screen          Globe+L
Siri                 Fn+S
Accessibility        Globe+X
Rotate device        Globe+R
Keyboard gestures    Globe+G
```

Decision rules:

1. Launch an app with `Cmd+Space`, type its name, then Down + Enter.
2. Navigate and activate with Tab/arrows/Space before touching the pointer.
3. Use Home (`Cmd+H`) or the app switcher instead of clicking system chrome.
4. If a keyboard command is unsupported, say so and fall back to the pointer.
5. Never claim a keyboard launch worked without checking the resulting screen.
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
