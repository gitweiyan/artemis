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

    def open_app(self, app: str, *, query: str | None = None, capture=None, verify=None) -> AppLaunchResult:
        query = (query or app).strip()
        if not query:
            return AppLaunchResult(False, app, query, error="empty_query")
        try:
            # Dismiss Spotlight/a sheet, then start from Home so Cmd+Space always
            # opens a fresh search rather than toggling an old overlay closed.
            self.control.press_named_key("escape")
            self.control.ios_home()
            time.sleep(self.wait_s)
            before = capture.grab() if capture is not None else None

            self.control.press_named_key("spotlight")
            time.sleep(self.wait_s)
            self.control.type_text(query)
            time.sleep(self.wait_s)
            self.control.press_named_key("down")
            self.control.press_named_key("enter")
            time.sleep(self.wait_s)

            if capture is None:
                return AppLaunchResult(True, app, query)
            after = capture.grab()
            if verify is None:
                ok = changed(before.image, after.image)
            else:
                ok = bool(verify(before.image, after.image))
            if not ok:
                return AppLaunchResult(False, app, query, error="no_screen_change")
            return AppLaunchResult(True, app, query)
        except Exception as exc:  # HID/capture failure is data, not a crash
            return AppLaunchResult(False, app, query, error=f"{type(exc).__name__}: {exc}")
