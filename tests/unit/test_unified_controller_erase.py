"""erase_text prefers a driver-level platform gesture over the Android shell fallback."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from third_party.mobile_use.controllers.unified_controller import UnifiedMobileControllerBase


def test_erase_text_uses_the_driver_implementation():
    calls = []

    class Driver:
        async def erase_text(self, nb_chars=None):
            calls.append(nb_chars)
            return True

    controller = UnifiedMobileControllerBase(SimpleNamespace(), Driver())

    assert asyncio.run(controller.erase_text()) is True
    assert calls == [None]


def test_erase_text_falls_back_to_the_android_shell():
    class Driver:
        async def execute_shell(self, command):
            return ""

        async def press_key(self, keycode):
            return True

    controller = UnifiedMobileControllerBase(SimpleNamespace(), Driver())

    assert asyncio.run(controller.erase_text()) is True
