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

"""``artemis ios-calibrate``: measure this rig's pointer gain instead of guessing it.

Prints the per-band gains (small moves vs large moves, i.e. how hard the rig accelerates)
and the ``ARTEMIS_IOS_POINTER_GAIN`` line to put in the environment. The tap loop estimates
the ratio online either way; a measured starting value simply costs fewer correction steps.
"""

import asyncio
import os
import json
from typing import Annotated

from rich.console import Console
import typer

from artemis.devices.descriptors import build_ios_descriptor
from artemis.devices.descriptors import device_alias
from artemis.drivers.ios.uvc_identity import resolve_uvc_identity
from artemis.devices.ios_uvc import build_ios_uvc_session
from artemis.drivers.ios.calibration import calibrate_pointer
from artemis.drivers.ios.config import IosConfigError, IosRuntimeConfig
from artemis.drivers.ios.ios_uvc_hid_driver import IOSUvcHidDriver
from artemis.perception.ocr_engine import default_ocr_engine
from artemis.reflex.capture import UvcCapture, UvcStream
from artemis.reflex.control import Esp32HidControl
from artemis.runtime.host_identity import host_id as resolve_host_id
from artemis.runtime.resource_lease import LeaseSet, ResourceBusyError, leases_enabled
from third_party.mobile_use.utils.logger import get_logger

logger = get_logger(__name__)


def ios_calibrate_command(
    repeat: Annotated[
        int,
        typer.Option("--repeat", min=1, max=5, help="Probes per move size (more = steadier)."),
    ] = 2,
    as_json: Annotated[bool, typer.Option("--json", help="Print the measurement as JSON.")] = False,
) -> None:
    """Measure the iOS pointer gain (two bands) and print the env line to use."""
    console = Console()
    try:
        config = IosRuntimeConfig.from_env()
    except IosConfigError as exc:
        console.print(f"[bold red]✖ iOS rig is not configured:[/bold red] {exc}")
        raise SystemExit(2)

    host = resolve_host_id()
    uvc_identity = resolve_uvc_identity(
        config.uvc_device,
        explicit_key=(os.environ.get("ARTEMIS_UVC_IDENTITY_KEY") or "").strip() or None,
        strict=(os.environ.get("ARTEMIS_UVC_IDENTITY_STRICT") or "").strip() == "1",
    )
    descriptor = build_ios_descriptor(
        device_id="ios-uvc",
        host_id=host,
        screen_size=config.screen_size,
        uvc_device=config.uvc_device,
        esp32_port=config.esp32_port,
        alias=device_alias(),
        uvc_hardware_key=uvc_identity.key,
        uvc_identity_source=uvc_identity.source,
    )
    leases = LeaseSet(host_id=host, description="ios.calibrate")
    if leases_enabled():
        try:
            leases.acquire(descriptor.resource_ids)
        except ResourceBusyError as exc:
            console.print(f"[bold red]✖ iOS rig is busy:[/bold red] {exc}")
            raise SystemExit(2)

    capture = None
    control = None
    session = None
    try:
        capture_factory = UvcStream if config.capture_mode == "stream" else UvcCapture
        capture = capture_factory(
            config.uvc_device,
            size=config.uvc_size,
            framerate=30,
            crop=config.uvc_crop,
            timeout_s=3.0 if config.capture_mode == "stream" else 12.0,
        )
        control = Esp32HidControl(config.esp32_port, ack_timeout_s=3.0)
        session = build_ios_uvc_session(
            capture,
            control,
            frame_size=config.frame_size,
            screen_size=config.screen_size,
            pointer_gain=config.pointer_gain,
            ocr_engine=default_ocr_engine() if config.enable_ocr else None,
            descriptor=descriptor,
            leases=leases,
        )
        driver = IOSUvcHidDriver(session)
        controller = driver._relative_sink().controller

        console.print(f"[dim]{config.describe()}[/dim]")
        result = asyncio.run(calibrate_pointer(controller, repeats=repeat))

        if as_json:
            console.print_json(
                json.dumps(
                    {
                        "suggested": list(result.suggested) if result.suggested else None,
                        "small_gain": result.small_gain,
                        "large_gain": result.large_gain,
                        "acceleration_ratio": result.acceleration_ratio,
                        "notes": list(result.notes),
                        "env": result.env_line(),
                    },
                    ensure_ascii=False,
                )
            )
        else:
            for line in result.describe().splitlines():
                console.print(line)

        if result.suggested is None:
            raise SystemExit(1)
    finally:
        if session is not None:
            asyncio.run(session.close())
        else:
            for resource in (control, capture):
                closer = getattr(resource, "close", None)
                if callable(closer):
                    closer()
            leases.release()
