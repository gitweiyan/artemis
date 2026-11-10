# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The Android driver is a façade that also exposes its composition as a DeviceSession."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from artemis.drivers.android.adb_driver import AndroidAdbDriver


def _driver() -> AndroidAdbDriver:
    return AndroidAdbDriver(
        device_id="emulator-5554",
        adb_client=SimpleNamespace(),
        ui_adb_client=None,
        width=1080,
        height=2400,
    )


def test_the_driver_exposes_an_app_session_it_builds_once():
    driver = _driver()

    session = driver.session

    assert session is driver.session, "the composition is built lazily and cached"
    assert session.capabilities.supports_ui_tree is True
    assert session.capabilities.supports_absolute_tap is True
    assert session.capabilities.requires_pointer_precheck is False, (
        "absolute control never needs a pointer precheck"
    )
    assert session.geometry.screen_size == (1080, 2400)


def test_the_session_control_reaches_the_driver_itself():
    driver = _driver()
    session = driver.session
    seen: list[tuple[int, int]] = []

    async def fake_tap(x, y, **kwargs):
        seen.append((x, y))
        return True

    with patch.object(driver, "tap", side_effect=fake_tap, create=True):
        import asyncio

        result = asyncio.run(session.control_for(absolute=True).tap_abs(540, 1200))

    assert result.ok is True
    assert seen == [(540, 1200)]


def test_the_factory_logs_the_android_composition(monkeypatch, caplog):
    import logging

    from artemis.drivers.factory import create_driver

    ctx = SimpleNamespace(
        device=SimpleNamespace(
            device_id="emulator-5554",
            device_width=1080,
            device_height=2400,
            mobile_platform="android",
        ),
        adb_client=SimpleNamespace(),
        ui_adb_client=None,
    )

    with caplog.at_level(logging.INFO):
        create_driver(ctx)

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "Composition: app" in logged
    assert "UI tree" in logged or "tree" in logged


def test_driver_tap_goes_through_the_session_control_sink():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from artemis.control.base import ActionResult

    driver = _driver()
    session = driver.session
    sink = MagicMock()
    sink.tap_abs = AsyncMock(return_value=ActionResult(True, "tap_abs"))

    with patch.object(session, "control_for", return_value=sink) as control_for:
        ok = asyncio.run(driver.tap(540, 1200))

    assert ok is True
    control_for.assert_called_once_with(absolute=True)
    sink.tap_abs.assert_awaited_once_with(540, 1200)


def test_a_session_dispatch_uses_the_legacy_driver_instead_of_recursing():
    import asyncio
    from unittest.mock import MagicMock

    driver = _driver()
    driver._device = MagicMock()
    driver._session_dispatch_depth = 1

    ok = asyncio.run(driver.tap(540, 1200))

    assert ok is True
    driver.device.shell.assert_called_with("input tap 540 1200")
    assert driver._session_dispatch_depth == 1


def test_the_legacy_controller_now_reaches_the_session_through_the_driver():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from artemis.control.base import ActionResult
    from artemis.controllers.unified_controller import UnifiedMobileController

    driver = _driver()
    driver._device = MagicMock()
    ctx = SimpleNamespace(device=driver, _active_driver=driver)
    controller = UnifiedMobileController(ctx)
    sink = MagicMock()
    sink.tap_abs = AsyncMock(return_value=ActionResult(True, "tap_abs"))

    with patch.object(driver.session, "control_for", return_value=sink) as control_for:
        result = asyncio.run(controller.tap_at(540, 1200))

    assert result.error is None
    control_for.assert_called_once_with(absolute=True)
    sink.tap_abs.assert_awaited_once_with(540, 1200)
