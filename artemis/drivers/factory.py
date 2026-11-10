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
from dataclasses import replace
from typing import TYPE_CHECKING

from adbutils import AdbClient

from artemis._version import get_version
from artemis.config import settings
from artemis.core.capability_registry import CapabilityRegistry
from artemis.data_engine.capability_evidence import (
    SqliteCapabilityEvidenceStore,
    capability_evidence_enabled,
)
from artemis.devices.composition import CompositionRequest
from artemis.devices.descriptors import (
    build_mock_descriptor,
    capability_fields_for,
    descriptor_from_device_context,
    device_alias,
)
from artemis.drivers.android.adb_driver import AndroidAdbDriver
from artemis.drivers.base import BaseDeviceDriver
from artemis.drivers.mock.mock_driver import MockDeviceDriver
from artemis.perception.ocr_engine import default_ocr_engine
from artemis.devices.registry import (
    ProfileBuild,
    ProfileBuildError,
    builtin_registry,
    select_device_profile,
)
from artemis.runtime.host_identity import host_id as resolve_host_id
from artemis.runtime.resource_lease import LeaseSet, leases_enabled
from third_party.mobile_use.utils.logger import get_logger

if TYPE_CHECKING:
    from artemis.context import ArtemisContext

logger = get_logger(__name__)


def _describe_capabilities(capabilities) -> str:
    """The capability flags that decide perception/control composition, for the log."""
    return ", ".join(
        f"{name}={getattr(capabilities, name)}" for name in capabilities.__dataclass_fields__
    )


def _close_quietly(obj) -> None:
    closer = getattr(obj, "close", None)
    if not callable(closer):
        return
    try:
        closer()
    except (OSError, RuntimeError, ValueError, AttributeError) as exc:
        logger.debug(f"Closing {type(obj).__name__} during factory cleanup failed: {exc}")


def _attach_descriptor(ctx: "ArtemisContext", descriptor) -> None:
    """Record the read-only descriptor on the context (never selects a driver)."""

    ctx.device_descriptor = descriptor
    logger.info(
        f"Device descriptor: {descriptor.device_id}"
        f" ({descriptor.platform_family.value}, {len(descriptor.endpoints)} endpoint(s))"
    )


def _descriptor_screen_size(ctx: "ArtemisContext") -> tuple[int, int] | None:
    """The device descriptor's screen size, when it is usable."""

    device = getattr(ctx, "device", None)
    try:
        width = int(getattr(device, "device_width", 0) or 0)
        height = int(getattr(device, "device_height", 0) or 0)
    except (TypeError, ValueError):
        return None
    return (width, height) if width > 0 and height > 0 else None


def build_ios_profile(runtime, request, registry):
    ctx = runtime
    if ctx is None:
        raise ProfileBuildError(
            "runtime_required", "iOS profile needs a runtime context", profile_id="ios.uvc_hid"
        )

    from artemis.devices.ios_uvc import build_ios_uvc_session
    from artemis.drivers.ios.config import IosRuntimeConfig
    from artemis.drivers.ios.ios_uvc_hid_driver import IOSUvcHidDriver
    from artemis.orchestration.selector import (
        CompositionError,
        DeviceProfile,
        select_composition,
    )
    from artemis.reflex.capture import UvcCapture, UvcStream
    from artemis.adapters.esp32_control import Esp32BridgeControl

    ios = IosRuntimeConfig.from_env()
    if not ios.uvc_crop_explicit and ios.capture_mode != "stream":
        # The crop is a property of where the phone sits on the card, not of this
        # code. Measure it once from a native frame instead of trusting a constant
        # that a re-seat or a card swap silently invalidates; an explicit
        # ARTEMIS_UVC_CROP always wins (a bad measurement is worse than a pin).
        from artemis.drivers.ios.crop import detect_uvc_crop

        measured, why = detect_uvc_crop(device=ios.uvc_device, size=ios.uvc_size)
        if measured is not None:
            ios = replace(ios, uvc_crop=measured)
            logger.info(
                f"UV C crop measured from a frame: {measured} ({why}); pin it with ARTEMIS_UVC_CROP"
            )
        else:
            logger.warning(f"UV C crop could not be measured ({why}); using {ios.uvc_crop}")
    elif not ios.uvc_crop_explicit:
        # Stream mode owns one persistent capture; a second one-shot grab to measure
        # the crop would defeat the point. Keep the pinned value and let the smoke's
        # frame_fit stage report a crop that no longer frames the phone.
        logger.info(
            "UV C crop stays as configured (stream mode): run ARTEMIS_IOS_CAPTURE=frame"
            " artemis ios-smoke to measure it"
        )
    composition = select_composition(
        DeviceProfile(
            platform="ios",
            device_id=getattr(ctx.device, "device_id", "ios-uvc"),
            capabilities=IOSUvcHidDriver.capabilities,
            screen_size=_descriptor_screen_size(ctx) or ios.screen_size,
            uvc_available=True,
            camera_available=bool(os.environ.get("ARTEMIS_IOS_CAMERA", "").strip()),
            capture_mode=ios.capture_mode,
        )
    )
    logger.info(f"Composition: {composition.kind} — {composition.reason}")
    if composition.kind != "uvc-hid":
        # Only the UVC + HID builder exists today; saying so beats pretending.
        raise CompositionError(
            f"composition {composition.kind!r} is not buildable yet ({composition.reason})"
        )

    host = resolve_host_id()
    session_id = os.environ.get("ARTEMIS_SESSION_ID") or None
    descriptor = descriptor_from_device_context(
        ctx,
        host_id=host,
        alias=device_alias(),
        ios_config=ios,
    )
    descriptor = replace(
        descriptor,
        fingerprint=capability_fields_for(
            descriptor,
            adapter_id="esp32_hid",
            adapter_version=get_version(),
        ),
    )
    leases = LeaseSet(
        host_id=host,
        description="ios.uvc_hid",
        session_id=session_id,
    )
    if leases_enabled():
        leases.acquire(descriptor.resource_ids)

    capture = None
    control = None
    completed = False
    try:
        capture_factory = UvcStream if ios.capture_mode == "stream" else UvcCapture
        capture = capture_factory(
            ios.uvc_device,
            size=ios.uvc_size,
            framerate=30,
            crop=ios.uvc_crop,
            timeout_s=3.0 if ios.capture_mode == "stream" else 12.0,
        )
        control = Esp32BridgeControl(ios.esp32_port, ack_timeout_s=3.0)
        # The action layer converts normalized coordinates against the *device
        # descriptor*, so the session geometry must use the same screen size or every
        # tap lands off by the ratio. The descriptor is itself derived from this config
        # (ios_device_context); a disagreement is a bug worth seeing in the log.
        screen_size = _descriptor_screen_size(ctx) or ios.screen_size
        if screen_size != ios.screen_size:
            logger.warning(
                f"iOS descriptor screen size {screen_size} differs from configured"
                f" {ios.screen_size}; using the descriptor (the action layer's frame)."
            )
        session = build_ios_uvc_session(
            capture,
            control,
            frame_size=ios.frame_size,
            screen_size=screen_size,
            pointer_gain=ios.pointer_gain,
            ocr_engine=default_ocr_engine() if ios.enable_ocr else None,
            descriptor=descriptor,
            leases=leases,
            session_id=session_id,
        )
        if capability_evidence_enabled():
            try:
                session.capability_registry = CapabilityRegistry(SqliteCapabilityEvidenceStore())
            except (OSError, RuntimeError, ValueError) as exc:
                logger.warning(f"Capability evidence store unavailable: {exc}")
        driver = IOSUvcHidDriver(session)
        completed = True
    finally:
        if not completed:
            _close_quietly(control)
            _close_quietly(capture)
            leases.release()

    ctx.device_session = session
    _attach_descriptor(ctx, descriptor)
    logger.info(
        f"Device driver: {driver.device_id} ({ios.describe()}); "
        f"capabilities: {_describe_capabilities(driver.capabilities)}"
    )
    return ProfileBuild(profile_id="ios.uvc_hid", session=session, driver=driver, leases=leases)


def build_mock_profile(runtime, request, registry):
    ctx = runtime
    device = getattr(ctx, "device", None)
    device_id = getattr(device, "device_id", None) or request.device_id or "mock-device"
    width, height = request.screen_size or (
        (
            getattr(device, "device_width", 0) or 1080,
            getattr(device, "device_height", 0) or 2400,
        )
        if device
        else (1080, 2400)
    )
    driver = MockDeviceDriver(device_id=device_id, width=width, height=height)
    from artemis.devices.app_session import build_app_session

    session = build_app_session(driver, screen_size=(width, height))
    if ctx is not None:
        _attach_descriptor(
            ctx,
            build_mock_descriptor(
                device_id=device_id,
                screen_size=(width, height),
                alias=device_alias(),
            ),
        )
    return ProfileBuild(profile_id="mock.screen", session=session, driver=driver)


def build_android_profile(runtime, request, registry):
    ctx = runtime
    if ctx is None:
        raise ProfileBuildError(
            "runtime_required", "Android profile needs a runtime context", profile_id="android.app"
        )

    if ctx.adb_client is None:
        ctx.adb_client = AdbClient(
            host=settings.ADB_HOST or "localhost", port=settings.ADB_PORT or 5037
        )

    driver = AndroidAdbDriver(
        device_id=ctx.device.device_id,
        adb_client=ctx.adb_client,
        ui_adb_client=getattr(ctx, "ui_adb_client", None),
        width=ctx.device.device_width,
        height=ctx.device.device_height,
    )
    from artemis.orchestration.selector import DeviceProfile, select_composition

    composition = select_composition(
        DeviceProfile(
            platform="android",
            device_id=driver.device_id,
            capabilities=driver.capabilities,
            screen_size=driver.screen_size,
        )
    )
    logger.info(f"Composition: {composition.kind} — {composition.reason}")
    _attach_descriptor(
        ctx,
        descriptor_from_device_context(
            ctx,
            host_id=resolve_host_id(),
            alias=device_alias(),
            adb_host=settings.ADB_HOST or "127.0.0.1",
            adb_port=settings.ADB_PORT or 5037,
        ),
    )
    return ProfileBuild(profile_id="android.app", driver=driver)


def _composition_request(ctx: "ArtemisContext") -> CompositionRequest:
    from artemis.core.capabilities import CapabilityId
    from artemis.devices.capabilities import CapabilitySet
    from artemis.devices.composition import CompositionRequest

    device = getattr(ctx, "device", None)
    raw = getattr(device, "mobile_platform", "android") if device else "android"
    platform = str(getattr(raw, "value", raw) or "android").lower()
    if os.environ.get("ARTEMIS_MOCK_DRIVER") == "1":
        platform = "mock"

    if platform == "ios":
        from artemis.drivers.ios.ios_uvc_hid_driver import IOSUvcHidDriver

        capabilities = IOSUvcHidDriver.capabilities
        capability_ids = frozenset(
            {
                CapabilityId.FRAME_CAPTURE,
                CapabilityId.POINTER_RELATIVE,
                CapabilityId.USB_HID,
                CapabilityId.SERIAL_COMMAND,
            }
        )
        profile_id = "ios.uvc_hid"
        uvc_available = True
        capture_mode = (os.environ.get("ARTEMIS_IOS_CAPTURE") or "frame").strip().lower()
    elif platform == "mock":
        capabilities = CapabilitySet()
        capability_ids = frozenset({CapabilityId.FRAME_CAPTURE})
        profile_id = "mock.screen"
        uvc_available = False
        capture_mode = "frame"
    else:
        from artemis.drivers.android.adb_driver import AndroidAdbDriver

        capabilities = AndroidAdbDriver.capabilities
        capability_ids = frozenset(
            {
                CapabilityId.UI_TREE_READ,
                CapabilityId.TOUCH_ABSOLUTE,
                CapabilityId.TEXT_INPUT,
                CapabilityId.APP_LAUNCH,
                CapabilityId.SHELL_EXEC,
            }
        )
        profile_id = "android.app"
        uvc_available = False
        capture_mode = "n/a"

    return CompositionRequest(
        platform=platform,
        device_id=str(getattr(device, "device_id", "default") or "default"),
        capabilities=capabilities,
        screen_size=_descriptor_screen_size(ctx),
        uvc_available=uvc_available,
        capture_mode=capture_mode,
        profile_id=profile_id,
        capability_ids=capability_ids,
    )


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

    # ADR-005 Phase 2c: the factory only selects a registered profile and calls its
    # builder; platform/config/handle code lives in build_*_profile below.
    request = _composition_request(ctx)
    registry = builtin_registry(runtime=ctx)
    selection = select_device_profile(request, registry)
    profile = selection.profile
    if profile.build is None:
        raise ProfileBuildError(
            "builder_not_registered",
            f"profile {profile.profile_id!r} has no builder",
            profile_id=profile.profile_id,
        )
    build = profile.build(request, registry)
    if build.driver is None:
        raise ProfileBuildError(
            "driver_missing",
            f"profile {profile.profile_id!r} built no driver",
            profile_id=profile.profile_id,
        )
    return build.driver


def get_driver(ctx: "ArtemisContext") -> BaseDeviceDriver:
    """Cached accessor for device driver in the current context."""
    if not hasattr(ctx, "_active_driver") or getattr(ctx, "_active_driver") is None:
        driver = create_driver(ctx)
        setattr(ctx, "_active_driver", driver)
    return getattr(ctx, "_active_driver")
