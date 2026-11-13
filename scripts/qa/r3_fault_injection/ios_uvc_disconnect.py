"""R3.8 iOS UVC unplug acceptance runner.

This is an operator-assisted test: it establishes a real UVC + ESP32 Session,
waits for the operator to unplug only the UVC capture USB, verifies the Session
enters FAILED, closes it, and prints the cleanup report. It never unplugs hardware
itself.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import os
import time

from artemis._version import get_version
from artemis.adapters.esp32_control import Esp32BridgeControl
from artemis.adapters.uvc import UvcCapture
from artemis.config import get_temp_dir
from artemis.core.identity import IdentitySource
from artemis.devices.descriptors import build_ios_descriptor, capability_fields_for, device_alias
from artemis.devices.ios_uvc import build_ios_uvc_session
from artemis.drivers.ios.config import IosRuntimeConfig
from artemis.drivers.ios.uvc_identity import resolve_uvc_identity
from artemis.perception.ocr_engine import default_ocr_engine
from artemis.runtime.host_identity import host_id as resolve_host_id
from artemis.runtime.resource_lease import LeaseSet, leases_enabled


def _build(config: IosRuntimeConfig):
    host = resolve_host_id()
    identity = resolve_uvc_identity(
        config.uvc_device,
        explicit_key=os.environ.get("ARTEMIS_UVC_IDENTITY_KEY") or None,
        strict=os.environ.get("ARTEMIS_UVC_IDENTITY_STRICT") == "1",
        host_id=host,
        port_hint=config.uvc_port_hint,
    )
    descriptor = build_ios_descriptor(
        device_id="ios-uvc",
        host_id=host,
        screen_size=config.screen_size,
        uvc_device=config.uvc_device,
        esp32_port=config.esp32_port,
        alias=device_alias(),
        uvc_hardware_key=identity.key,
        uvc_identity_source=identity.source,
    )
    descriptor = replace(
        descriptor,
        fingerprint=capability_fields_for(
            descriptor, adapter_id="esp32_hid", adapter_version=get_version()
        ),
    )
    leases = LeaseSet(host_id=host, description="r3.8.ios_uvc_disconnect")
    if leases_enabled():
        leases.acquire(descriptor.resource_ids)
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
    return session, capture, control, leases


async def _run(args: argparse.Namespace) -> int:
    config = IosRuntimeConfig.from_env()
    session = capture = control = leases = None
    try:
        session, capture, control, leases = _build(config)
        session.open()
        frame = await session.snapshot()
        print(
            f"SESSION_READY session={session.session_id} frame={frame.frame_id}"
            f" size={frame.width}x{frame.height}",
            flush=True,
        )
        print(
            "ACTION: unplug ONLY the UVC capture-card USB; leave ESP32 connected."
            f" Waiting up to {args.timeout}s...",
            flush=True,
        )
        deadline = time.monotonic() + args.timeout
        fault: Exception | None = None
        fault_count = 0
        while time.monotonic() < deadline:
            try:
                await session.snapshot()
            except Exception as exc:  # the fault is the expected result
                fault = fault or exc
                fault_count += 1
                if session.state.value == "failed":
                    break
                if fault_count >= 6:
                    break
            else:
                fault_count = 0
            await asyncio.sleep(0.5)
        if fault is None:
            print("RESULT: FAIL no fault detected before timeout", flush=True)
            return 2
        if session.state.value != "failed":
            print(
                f"RESULT: FAIL fault observed {fault_count} time(s) but session"
                f" state={session.state.value}",
                flush=True,
            )
            return 3
        print(
            f"FAULT_DETECTED state={session.state.value}"
            f" fault_source={session.fault_source!r}"
            f" reason={session.fault_reason!r} error={type(fault).__name__}: {fault}",
            flush=True,
        )
        report = await session.close()
        print(
            "SESSION_CLOSED"
            f" state={report.session_state.value}"
            f" cleanup={report.cleanup_state.value}"
            f" released={list(report.released_now)}"
            f" retained={list(report.retained)}"
            f" complete={report.cleanup_complete}",
            flush=True,
        )
        print("ACTION: reconnect the UVC USB, then start a new Session smoke.", flush=True)
        return 0 if report.cleanup_complete else 1
    finally:
        if session is not None and session.state.value not in {"closed", "closing"}:
            try:
                await session.close()
            except Exception:
                pass
        elif session is None:
            for item in (control, capture):
                closer = getattr(item, "close", None)
                if callable(closer):
                    closer()
        if session is None and leases is not None:
            leases.release()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--timeout",
        type=float,
        default=90.0,
        help="Seconds to wait for the operator to unplug UVC.",
    )
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
