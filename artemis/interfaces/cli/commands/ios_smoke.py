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

"""``artemis ios-smoke``: prove the iOS rig before blaming the agent.

Runs the same chain a task run uses — UVC frame, OCR locate, pointer precheck, HID
tap — with no LLM and no plan. Exits non-zero on the first broken stage so it can gate
a real-device run in a script.
"""

import asyncio
from dataclasses import replace
import json
from typing import Annotated

from rich.console import Console
import typer

from artemis._version import get_version
from artemis.core.capability_registry import CapabilityRegistry
from artemis.data_engine.capability_evidence import (
    SqliteCapabilityEvidenceStore,
    capability_evidence_enabled,
)
from artemis.devices.descriptors import (
    build_ios_descriptor,
    capability_fields_for,
    device_alias,
)
from artemis.devices.ios_uvc import build_ios_uvc_session
from artemis.drivers.ios.config import IosConfigError, IosRuntimeConfig
from artemis.drivers.ios.ios_uvc_hid_driver import IOSUvcHidDriver
from artemis.drivers.ios.evidence import record_smoke_evidence
from artemis.drivers.ios.smoke import run_smoke
from artemis.perception.ocr_engine import default_ocr_engine
from artemis.reflex.capture import UvcCapture
from artemis.adapters.esp32_control import Esp32BridgeControl
from artemis.runtime.host_identity import host_id as resolve_host_id
from artemis.runtime.resource_lease import LeaseSet, ResourceBusyError, leases_enabled
from third_party.mobile_use.utils.logger import get_logger

logger = get_logger(__name__)


def ios_smoke_command(
    target: Annotated[
        str | None,
        typer.Option(
            "--target",
            "-t",
            help="Text label to locate with OCR and tap (e.g. 设置 or 闪购).",
        ),
    ] = None,
    no_tap: Annotated[
        bool,
        typer.Option(
            "--no-tap",
            help="Observe and locate only; never click.",
        ),
    ] = False,
    as_json: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Print the report as JSON (for CI or a bug report).",
        ),
    ] = False,
) -> None:
    """Smoke-test the iOS UVC + ESP32 HID rig (no LLM, no task plan)."""
    console = Console()

    try:
        config = IosRuntimeConfig.from_env()
    except IosConfigError as exc:
        console.print(f"[bold red]✖ iOS rig is not configured:[/bold red] {exc}")
        raise SystemExit(2)

    console.print(f"[dim]{config.describe()}[/dim]")

    host = resolve_host_id()
    descriptor = build_ios_descriptor(
        device_id="ios-uvc",
        host_id=host,
        screen_size=config.screen_size,
        uvc_device=config.uvc_device,
        esp32_port=config.esp32_port,
        alias=device_alias(),
    )
    descriptor = replace(
        descriptor,
        fingerprint=capability_fields_for(
            descriptor,
            adapter_id="esp32_hid",
            adapter_version=get_version(),
        ),
    )
    store = None
    if capability_evidence_enabled():
        try:
            store = SqliteCapabilityEvidenceStore()
        except (OSError, RuntimeError, ValueError) as exc:
            console.print(f"[yellow]⚠ capability evidence store unavailable: {exc}[/yellow]")
    leases = LeaseSet(host_id=host, description="ios.smoke")
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
        capture = UvcCapture(
            config.uvc_device,
            size=config.uvc_size,
            framerate=30,
            crop=config.uvc_crop,
            timeout_s=12,
        )
        control = Esp32BridgeControl(config.esp32_port, ack_timeout_s=3.0)
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
        if store is not None:
            session.capability_registry = CapabilityRegistry(store)
        driver = IOSUvcHidDriver(session)
        _smoke_or_exit(
            console,
            driver,
            session,
            target=target,
            no_tap=no_tap,
            as_json=as_json,
            store=store,
            descriptor=descriptor,
            test_version=get_version(),
            capture_mode=config.capture_mode,
        )
    finally:
        if session is not None:
            asyncio.run(session.close())
        else:
            for resource in (control, capture):
                closer = getattr(resource, "close", None)
                if callable(closer):
                    closer()
            leases.release()


def _smoke_or_exit(
    console,
    driver,
    session,
    *,
    target,
    no_tap,
    as_json,
    store=None,
    descriptor=None,
    test_version="unknown",
    capture_mode=None,
):
    if target and not session.capabilities.supports_ocr:
        console.print(
            "[bold red]✖ --target needs an OCR engine, but none is available[/bold red]"
            " (install paddleocr, or run on a Mac with Vision; ARTEMIS_IOS_OCR=0 also"
            " disables it)"
        )
        raise SystemExit(2)

    report = asyncio.run(run_smoke(driver, target=target, tap=not no_tap))

    evidence_ids: tuple[str, ...] = ()
    if store is not None and descriptor is not None:
        sink = session.control_for(absolute=False)
        controller = getattr(sink, "controller", None)
        absolute_ready = bool(getattr(controller, "absolute_ready", False))
        try:
            evidence_ids = record_smoke_evidence(
                store=store,
                descriptor=descriptor,
                report=report,
                test_version=test_version,
                absolute_ready=absolute_ready,
                capture_mode=capture_mode,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            console.print(f"[yellow]⚠ capability evidence was not recorded: {exc}[/yellow]")

    if as_json:
        payload = report.to_dict()
        payload["evidence_ids"] = list(evidence_ids)
        console.print_json(json.dumps(payload, ensure_ascii=False))
    else:
        for line in report.summary().splitlines():
            console.print(line)

    if not report.ok:
        raise SystemExit(1)
    if evidence_ids:
        verified = sorted(
            str(record.capability_id)
            for record in session.capability_view.records
            if record.state == "verified"
        )
        console.print(
            f"[dim]capability evidence: {len(evidence_ids)} record(s);"
            f" verified now: {', '.join(verified) or 'none'}[/dim]"
        )
    console.print("[bold green]✅ iOS rig smoke passed[/bold green]")
