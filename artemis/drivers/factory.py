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

"""Device Driver Factory & Registry."""

import os
from typing import TYPE_CHECKING

from adbutils import AdbClient

from artemis.config import settings
from artemis.drivers.android.adb_driver import AndroidAdbDriver
from artemis.drivers.base import BaseDeviceDriver
from artemis.drivers.mock.mock_driver import MockDeviceDriver
from artemis.perception.paddle_ocr import PaddleOcrEngine
from third_party.mobile_use.utils.logger import get_logger

if TYPE_CHECKING:
    from artemis.context import ArtemisContext

logger = get_logger(__name__)


def _is_ios(ctx: "ArtemisContext") -> bool:
    platform = getattr(getattr(ctx, "device", None), "mobile_platform", None)
    return str(platform or "").lower() == "ios"


def _describe_capabilities(capabilities) -> str:
    """The capability flags that decide perception/control composition, for the log."""
    return ", ".join(f"{name}={getattr(capabilities, name)}" for name in capabilities.__dataclass_fields__)


def create_driver(ctx: "ArtemisContext") -> BaseDeviceDriver:
    """Instantiates the appropriate BaseDeviceDriver based on the runtime context."""
    # 1. Cloud mode check. Cloud devices are reached through the gateway's
    # RemoteUIAutomatorClient; ARTEMIS_HIERARCHY_BACKEND does not apply there
    # because the Accessibility Helper needs a local adb forward.
    if os.environ.get("ARTEMIS_CLOUD_MODE") == "1":
        if ctx.adb_client is None:
            from cloud_service.virtualization import RemoteAdbClient

            ctx.adb_client = RemoteAdbClient()
        if ctx.ui_adb_client is None:
            from cloud_service.virtualization import RemoteUIAutomatorClient

            ctx.ui_adb_client = RemoteUIAutomatorClient(adb_client=ctx.adb_client)

    # 2. iOS UVC + ESP32 HID. The rig is described entirely by validated config: a
    # missing port is a configuration error, never a silent fall through to ADB.
    if _is_ios(ctx):
        from artemis.devices.ios_uvc import build_ios_uvc_session
        from artemis.drivers.ios.config import IosRuntimeConfig
        from artemis.drivers.ios.ios_uvc_hid_driver import IOSUvcHidDriver
        from artemis.reflex.capture import UvcCapture
        from artemis.reflex.control import Esp32HidControl

        ios = IosRuntimeConfig.from_env()
        capture = UvcCapture(
            ios.uvc_device,
            size=ios.uvc_size,
            framerate=30,
            crop=ios.uvc_crop,
            timeout_s=12,
        )
        control = Esp32HidControl(ios.esp32_port, ack_timeout_s=3.0)
        session = build_ios_uvc_session(
            capture,
            control,
            frame_size=ios.frame_size,
            screen_size=ios.screen_size,
            pointer_gain=ios.pointer_gain,
            ocr_engine=PaddleOcrEngine() if ios.enable_ocr else None,
        )
        driver = IOSUvcHidDriver(session)
        logger.info(
            f"Device driver: {driver.device_id} ({ios.describe()}); "
            f"capabilities: {_describe_capabilities(driver.capabilities)}"
        )
        return driver

    # 3. Mock mode check
    if (
        getattr(ctx.device, "mobile_platform", None) == "mock"
        or os.environ.get("ARTEMIS_MOCK_DRIVER") == "1"
    ):
        return MockDeviceDriver(
            device_id=ctx.device.device_id if ctx.device else "mock-device",
            width=ctx.device.device_width if ctx.device else 1080,
            height=ctx.device.device_height if ctx.device else 2400,
        )

    # 3. Default Android ADB driver
    if ctx.adb_client is None:
        ctx.adb_client = AdbClient(
            host=settings.ADB_HOST or "localhost", port=settings.ADB_PORT or 5037
        )

    return AndroidAdbDriver(
        device_id=ctx.device.device_id,
        adb_client=ctx.adb_client,
        ui_adb_client=getattr(ctx, "ui_adb_client", None),
        width=ctx.device.device_width,
        height=ctx.device.device_height,
    )


def get_driver(ctx: "ArtemisContext") -> BaseDeviceDriver:
    """Cached accessor for device driver in the current context."""
    if not hasattr(ctx, "_active_driver") or getattr(ctx, "_active_driver") is None:
        driver = create_driver(ctx)
        setattr(ctx, "_active_driver", driver)
    return getattr(ctx, "_active_driver")
