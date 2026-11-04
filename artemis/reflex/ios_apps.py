"""Open iOS apps through Spotlight with the HID keyboard.

The relative pointer is the least reliable part of the iOS rig; Spotlight is a
keyboard-only path: Cmd+Space, type the app name, Down, Enter.  A capture can be
supplied to verify that the screen actually changed; without one the method only
reports that the key sequence was sent.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from artemis.reflex.vision import changed

__all__ = ["IOSAppLauncher", "AppLaunchResult"]


@dataclass(frozen=True)
class AppLaunchResult:
    ok: bool
    app: str
    query: str
    method: str = "spotlight"
    error: str | None = None


class IOSAppLauncher:
    """Keyboard-first iOS app launcher."""

    def __init__(self, control, *, wait_s: float = 0.7):
        self.control = control
        self.wait_s = max(0.0, float(wait_s))

    def open_app(
        self, app: str, *, query: str | None = None, capture=None, verify=None
    ) -> AppLaunchResult:
        query = (query or app).strip()
        if not query:
            return AppLaunchResult(False, app, query, error="empty_query")
        try:
            # Spotlight opens over the current screen: no Home/Escape reset, so a
            # launch does not discard the app the run was working in. Backspace
            # clears a query left over from an earlier Spotlight session (Cmd+Space
            # reopens the old search); Enter twice opens the highlighted result and
            # covers the cases where Spotlight still shows the query instead.
            before = capture.grab() if capture is not None else None

            self.control.press_named_key("spotlight")
            time.sleep(self.wait_s)
            self.control.press_named_key("backspace")
            time.sleep(self.wait_s)
            self.control.type_text(query)
            time.sleep(self.wait_s)
            self.control.press_named_key("enter")
            self.control.press_named_key("enter")
            time.sleep(self.wait_s)
            # A failed launch often leaves Spotlight open, and Spotlight-vs-Home is a
            # full-screen change: "the screen changed" alone reported success for an
            # app that never opened (2026-11-04 run, steps 1/15). Dismiss a leftover
            # Spotlight first, then require a substantial change from the pre-launch
            # screen.
            self.control.press_named_key("escape")
            time.sleep(self.wait_s)

            if capture is None:
                return AppLaunchResult(True, app, query)
            after = capture.grab()
            if verify is None:
                min_pixels = 3000
                width = getattr(after, "width", None) or getattr(before, "width", None)
                height = getattr(after, "height", None) or getattr(before, "height", None)
                if width and height:
                    min_pixels = max(3000, int(0.02 * int(width) * int(height)))
                ok = changed(
                    before.image, after.image, threshold=25, min_pixels=min_pixels
                )
            else:
                ok = bool(verify(before.image, after.image))
            if not ok:
                return AppLaunchResult(False, app, query, error="no_screen_change")
            return AppLaunchResult(True, app, query)
        except Exception as exc:  # HID/capture failure is data, not a crash
            return AppLaunchResult(False, app, query, error=f"{type(exc).__name__}: {exc}")
