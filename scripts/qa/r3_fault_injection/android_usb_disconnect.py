"""R3.8 Android USB ADB unplug acceptance runner.

Operator-assisted: establish an Android Session, wait for the target USB cable to
be unplugged, require the Session to reach FAILED after bounded observation
failures, then close and print the cleanup report.
"""

from __future__ import annotations

import argparse
import asyncio
import time

from adbutils import AdbClient

from artemis.context import ArtemisContext, DeviceContext
from artemis.drivers.factory import build_android_profile
from third_party.mobile_use.context import DevicePlatform


async def _run(args: argparse.Namespace) -> int:
    client = AdbClient(host="127.0.0.1", port=5037)
    device = client.device(args.serial)
    width, height = device.window_size()
    ctx = ArtemisContext(
        device=DeviceContext(
            mobile_platform=DevicePlatform.ANDROID,
            device_id=args.serial,
            device_width=width,
            device_height=height,
        ),
        adb_client=client,
    )
    build = build_android_profile(ctx, None, None)
    session = build.session
    try:
        session.open()
        frame = await session.snapshot()
        print(f"SESSION_READY frame={frame.frame_id} size={frame.width}x{frame.height}", flush=True)
        print(
            "ACTION: unplug the TARGET Android USB cable (not the Mac's network)."
            f" Waiting up to {args.timeout}s...",
            flush=True,
        )
        deadline = time.monotonic() + args.timeout
        fault = None
        count = 0
        while time.monotonic() < deadline:
            try:
                await session.snapshot()
            except Exception as exc:
                fault = fault or exc
                count += 1
                if session.state.value == "failed":
                    break
            else:
                count = 0
            await asyncio.sleep(0.5)
        if session.state.value != "failed":
            print(
                f"RESULT: FAIL no device fault before timeout state={session.state.value}"
                f" errors={count} last={fault!r}",
                flush=True,
            )
            report = await session.close()
            return 2
        print(
            f"FAULT_DETECTED state={session.state.value}"
            f" fault_source={session.fault_source!r} reason={session.fault_reason!r}"
            f" last_error={type(fault).__name__ if fault else None}: {fault}",
            flush=True,
        )
        report = await session.close()
        print(
            f"SESSION_CLOSED state={report.session_state.value}"
            f" cleanup={report.cleanup_state.value}"
            f" released={list(report.released_now)}"
            f" retained={list(report.retained)} complete={report.cleanup_complete}",
            flush=True,
        )
        return 0 if report.cleanup_complete else 1
    finally:
        if session.state.value not in {"closed", "closing"}:
            try:
                await session.close()
            except Exception:
                pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", default="EUWSOBXCZLPFNJF6")
    parser.add_argument("--timeout", type=float, default=120.0)
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
