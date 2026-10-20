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
import json
from typing import Annotated

from rich.console import Console
import typer

from artemis.devices.ios_uvc import build_ios_uvc_session
from artemis.drivers.ios.config import IosConfigError, IosRuntimeConfig
from artemis.drivers.ios.ios_uvc_hid_driver import IOSUvcHidDriver
from artemis.drivers.ios.smoke import run_smoke
from artemis.perception.paddle_ocr import PaddleOcrEngine
from artemis.reflex.capture import UvcCapture
from artemis.reflex.control import Esp32HidControl
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

    capture = UvcCapture(
        config.uvc_device,
        size=config.uvc_size,
        framerate=30,
        crop=config.uvc_crop,
        timeout_s=12,
    )
    control = Esp32HidControl(config.esp32_port, ack_timeout_s=3.0)
    session = build_ios_uvc_session(
        capture,
        control,
        frame_size=config.frame_size,
        screen_size=config.screen_size,
        pointer_gain=config.pointer_gain,
        ocr_engine=PaddleOcrEngine() if config.enable_ocr else None,
    )
    driver = IOSUvcHidDriver(session)

    if target and not config.enable_ocr:
        console.print("[bold red]✖ --target needs OCR, but ARTEMIS_IOS_OCR=0[/bold red]")
        raise SystemExit(2)

    report = asyncio.run(run_smoke(driver, target=target, tap=not no_tap))

    if as_json:
        console.print_json(json.dumps(report.to_dict(), ensure_ascii=False))
    else:
        for line in report.summary().splitlines():
            console.print(line)

    if not report.ok:
        raise SystemExit(1)
    console.print("[bold green]✅ iOS rig smoke passed[/bold green]")
