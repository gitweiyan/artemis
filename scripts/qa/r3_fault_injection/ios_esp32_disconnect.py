"""R3.8 iOS ESP32/control unplug acceptance runner.

Run with the iOS rig connected. At ``ACTION``, the operator unplugs ONLY the
ESP32-S3 OTG/USB connection; the UVC capture remains connected. The runner
repeatedly dispatches a harmless HOME key through the real ActionService until
the DeviceSession transitions to FAILED, then closes and reports cleanup.
"""

from __future__ import annotations

import argparse
import asyncio
import time

from artemis.context import ArtemisContext
from artemis.drivers.factory import build_ios_profile
from artemis.drivers.ios.config import IosRuntimeConfig
from artemis.interfaces.action_bootstrap import attach_action_service
from artemis.context import DeviceContext
from artemis.graph.state import State


async def _run(args: argparse.Namespace) -> int:
    config = IosRuntimeConfig.from_env()
    ctx = ArtemisContext(
        device=DeviceContext(
            mobile_platform="ios",
            device_id="ios-uvc",
            device_width=config.screen_size[0],
            device_height=config.screen_size[1],
        )
    )
    build = build_ios_profile(ctx, None, None)
    # The action executor's legacy controller resolves get_driver(ctx); reuse the
    # Session's driver instead of building a second iOS profile and re-leasing it.
    ctx._active_driver = build.driver
    attach_action_service(ctx)
    session = ctx.device_session
    action_service = ctx.action_service
    try:
        session.open()
        frame = await session.snapshot()
        print(
            f"SESSION_READY frame={frame.frame_id} state={session.state.value}",
            flush=True,
        )
        print(
            "ACTION: unplug ONLY the ESP32-S3 USB/OTG connection; leave UVC connected."
            f" Waiting up to {args.timeout}s...",
            flush=True,
        )
        deadline = time.monotonic() + args.timeout
        last_error = None
        action_state = State(initial_goal="r3.8 esp32 fault")
        while time.monotonic() < deadline:
            if session.state.value == "failed":
                break
            try:
                result = await action_service.execute(
                    "press_key", {"key": "HOME"}, "r3-esp32-fault", action_state
                )
                last_error = result.text_summary if result.status != "success" else None
            except Exception as exc:  # expected once the serial link is gone
                last_error = f"{type(exc).__name__}: {exc}"
            await asyncio.sleep(0.8)
        if session.state.value != "failed":
            print(
                f"RESULT: FAIL no control fault before timeout"
                f" state={session.state.value} last_error={last_error!r}",
                flush=True,
            )
            report = await session.close()
            return 2
        print(
            f"FAULT_DETECTED state={session.state.value}"
            f" fault_source={session.fault_source!r}"
            f" reason={session.fault_reason!r} last_error={last_error!r}",
            flush=True,
        )
        report = await session.close()
        print(
            f"SESSION_CLOSED state={report.session_state.value}"
            f" cleanup={report.cleanup_state.value}"
            f" released={list(report.released_now)}"
            f" retained={list(report.retained)}"
            f" complete={report.cleanup_complete}",
            flush=True,
        )
        return 0 if session.state.value == "closed" and report.cleanup_complete else 1
    finally:
        if action_service is not None:
            try:
                await action_service.close()
            except Exception:
                pass
        if session is not None and session.state.value not in {"closed", "closing"}:
            try:
                await session.close()
            except Exception:
                pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=90.0)
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
