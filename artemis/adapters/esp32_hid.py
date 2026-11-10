"""ESP32 HID command adapter over a FirmwareBridge (ADR-009 Phase 4.2b).

The adapter speaks the standard pointer/key/text vocabulary and owns the command
mapping (`move`/`amove`/`key`/`type`), segmentation and held-input tracking. It
does not know about UVC, iOS, the Agent or the Reflex loop; the legacy
``Esp32HidControl`` remains the compatibility path until Phase 4.2d.

Coordinates for ``move_abs`` are normalized 0..1000, matching the firmware's
``amove`` contract; screen scaling stays in the L3 Action layer.
"""

from __future__ import annotations

from typing import Any

__all__ = ["Esp32HidAdapter"]

_MAX_MOVE = 127


class Esp32HidAdapter:
    def __init__(self, bridge: Any, *, reference: tuple[int, int] = (1080, 2400)) -> None:
        self.bridge = bridge
        self.reference = (int(reference[0]), int(reference[1]))
        self._pressed_keys: set[int] = set()
        self._mouse_down = False
        self.info = None

    def preflight(self):
        self.info = self.bridge.preflight()
        return self.info

    def _send(self, command: str) -> str:
        return self.bridge.exchange(command)

    def move(self, dx: int, dy: int) -> None:
        remaining_x, remaining_y = int(dx), int(dy)
        while remaining_x or remaining_y:
            step_x = max(-_MAX_MOVE, min(_MAX_MOVE, remaining_x))
            step_y = max(-_MAX_MOVE, min(_MAX_MOVE, remaining_y))
            self._send(f"move {step_x} {step_y}")
            remaining_x -= step_x
            remaining_y -= step_y

    def move_abs(self, x: int, y: int) -> None:
        nx = max(0, min(1000, round(float(x))))
        ny = max(0, min(1000, round(float(y))))
        self._send(f"amove {nx} {ny}")

    def click(self) -> None:
        self._send("click")

    def click_abs(self) -> None:
        self._send("aclick")

    def button_down(self) -> None:
        self._send("mdown")
        self._mouse_down = True

    def button_up(self) -> None:
        self._send("mup")
        self._mouse_down = False

    def key(self, usage: int) -> None:
        self._send(f"key {int(usage)}")

    def key_down(self, usage: int) -> None:
        self._send(f"kdown {int(usage)}")
        self._pressed_keys.add(int(usage))

    def key_up(self, usage: int) -> None:
        self._send(f"kup {int(usage)}")
        self._pressed_keys.discard(int(usage))

    def keymod(self, modifier: int, usage: int) -> None:
        self._send(f"keymod {int(modifier)} {int(usage)}")

    def type_text(self, text: str) -> None:
        payload = str(text)
        if not payload.isascii():
            raise ValueError("ESP32 type_text only accepts ASCII; use pinyin/extension policy")
        # "type " + text + "\n" must fit the firmware line limit.
        chunk_size = max(1, int(getattr(self.bridge, "max_line", 126)) - len("type ") - 1)
        for start in range(0, len(payload), chunk_size):
            self._send(f"type {payload[start : start + chunk_size]}")

    def release_all(self) -> None:
        for usage in sorted(self._pressed_keys):
            try:
                self._send(f"kup {usage}")
            except (OSError, RuntimeError, ValueError):
                pass
        self._pressed_keys.clear()
        if self._mouse_down:
            try:
                self._send("mup")
            except (OSError, RuntimeError, ValueError):
                pass
            self._mouse_down = False

    def close(self) -> None:
        self.release_all()
        self.bridge.close()
