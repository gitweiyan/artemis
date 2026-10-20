"""A deterministic iOS rig smoke test: no LLM, no plan, one honest answer.

``artemis run --profile flash`` needs a model, a plan, and a phone. This runs the
same driver chain without any of the three, so an operator can answer one question
before blaming the agent: *does this rig see the screen, find a label, verify the
pointer, and land a click?*

Every step records what happened, including the incident when a tap does not fire.
The sequence is deliberately the same one the Validator relies on:
snapshot -> health -> OCR locate -> pointer precheck -> tap -> (controller's own diff).
"""

from __future__ import annotations

import time

from artemis.perception.incidents import UnhealthyFrame
from artemis.perception.text_locator import locate_text
from dataclasses import asdict, dataclass, field

__all__ = ["SmokeReport", "SmokeStep", "run_smoke"]


@dataclass(frozen=True)
class SmokeStep:
    """One observed stage of the smoke run."""

    name: str
    ok: bool
    detail: str
    elapsed_ms: float


@dataclass
class SmokeReport:
    """The whole smoke run, JSON-serializable for a bug report."""

    ok: bool
    steps: list[SmokeStep] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "steps": [asdict(step) for step in self.steps]}

    def summary(self) -> str:
        lines = []
        for step in self.steps:
            mark = "ok  " if step.ok else "FAIL"
            lines.append(f"[{mark}] {step.name} ({step.elapsed_ms:.0f} ms): {step.detail}")
        return "\n".join(lines)


async def run_smoke(
    driver, *, target: str | None = None, tap: bool = True, settle_s: float = 0.4
) -> SmokeReport:
    """Runs the rig smoke against ``driver`` (an ``IOSUvcHidDriver``).

    Continues after a failed *optional* stage (OCR, pointer, tap) so one report shows
    every stage; stops at the first failure that makes later ones meaningless (no frame).
    """
    report = SmokeReport(ok=True)
    session = driver.session
    if settle_s > 0:
        # The screen settles (page transitions, the pointer slam from home); a frame
        # taken mid-animation makes OCR read glyphs that are not there.
        await __import__("asyncio").sleep(settle_s)

    started = time.monotonic()
    try:
        await driver.connect()
        report.steps.append(
            SmokeStep(
                "connect", True, "pointer session connected", (time.monotonic() - started) * 1000
            )
        )
    except Exception as exc:  # noqa: BLE001 - a smoke step failure is data
        report.ok = False
        report.steps.append(
            SmokeStep("connect", False, str(exc), (time.monotonic() - started) * 1000)
        )
        return report

    started = time.monotonic()
    try:
        frame = await driver.snapshot_frame()
    except UnhealthyFrame as exc:
        report.ok = False
        report.steps.append(
            SmokeStep(
                "snapshot",
                False,
                f"{exc.incident}: {exc.detail}",
                (time.monotonic() - started) * 1000,
            )
        )
        return report
    except Exception as exc:  # noqa: BLE001 - a capture failure is data, not a crash
        report.ok = False
        report.steps.append(
            SmokeStep("snapshot", False, str(exc), (time.monotonic() - started) * 1000)
        )
        return report
    elapsed = (time.monotonic() - started) * 1000
    healthy = getattr(frame, "health", "ok") == "ok"
    report.ok = report.ok and healthy
    report.steps.append(
        SmokeStep(
            "snapshot",
            healthy,
            f"health={getattr(frame, 'health', '?')} size={frame.width}x{frame.height}",
            elapsed,
        )
    )
    if not healthy:
        return report

    # Does the pointer actually move? A rig can ACK every HID command while iOS has
    # dropped the assistive pointer device (seen live after a lock/unlock): clicks then
    # land at a stale position and no pointer ring is ever drawn. Diagnosing that here
    # is the difference between "the rig is broken" and "the task failed".
    started = time.monotonic()
    pointer_source = getattr(session, "pointer", None)
    moved = False
    detail = "session has no relative pointer to test"
    if pointer_source is not None:
        before = frame.image
        await __import__("asyncio").to_thread(pointer_source.move, 60, 60)
        deadline = time.monotonic() + 1.2
        while True:
            await __import__("asyncio").sleep(0.05)
            probe = await session.snapshot()
            if probe.image != before:
                moved = True
                break
            if time.monotonic() >= deadline:
                break
        detail = (
            "pointer moved (frame changed after a 60-unit move)"
            if moved
            else "HID accepted the move but the screen never changed: iOS is not drawing"
            " the pointer (replug the ESP32 OTG cable, or re-select the pointer device in"
            " Settings > Accessibility > Touch > AssistiveTouch > Devices)"
        )
    report.ok = report.ok and moved
    report.steps.append(
        SmokeStep("pointer_motion", moved, detail, (time.monotonic() - started) * 1000)
    )
    if not moved:
        return report

    located = None
    if target:
        started = time.monotonic()
        from artemis.perception.base import Target

        source = session.perception[0]
        boxes = None
        attempts = 0
        try:
            # A buffered frame can predate the screen settling (a move, a fade, a page
            # transition); OCR an unreadable frame is a retry, not a verdict.
            for attempts in range(1, 4):
                boxes = await source.ocr(frame)
                if boxes:
                    located = locate_text(boxes, target)
                    if located is not None:
                        break
                # A retry must read a *new* frame: an identical buffered one would give
                # the identical misread.
                await __import__("asyncio").sleep(0.35)
                frame = await session.snapshot()
        except Exception as exc:  # noqa: BLE001
            report.ok = False
            report.steps.append(
                SmokeStep(
                    "locate", False, f"OCR failed: {exc}", (time.monotonic() - started) * 1000
                )
            )
            return report
        elapsed = (time.monotonic() - started) * 1000
        if located is None:
            report.ok = False
            seen = ", ".join(box.text for box in sorted(boxes or [], key=lambda b: b.box[1])[:8])
            report.steps.append(
                SmokeStep(
                    "locate",
                    False,
                    f"text {target!r} not found in {len(boxes or [])} OCR boxes"
                    f" after {attempts} frame(s); saw: {seen}",
                    elapsed,
                )
            )
            return report
        report.steps.append(
            SmokeStep(
                "locate",
                True,
                f"{located.label!r} box={tuple(round(v, 3) for v in located.box)}"
                f" confidence={located.confidence:.2f}"
                + (f" (frame {attempts})" if attempts > 1 else ""),
                elapsed,
            )
        )

        started = time.monotonic()
        validation = await driver.validate_pointer(located)
        nudged = False
        if not validation.ok and validation.incident == "pointer_missing":
            # The AssistiveTouch ring fades out; a small relative move wakes it and is
            # exactly what the pointer loop does before every tap. Never a click.
            pointer = getattr(session, "pointer", None)
            if pointer is not None:
                await __import__("asyncio").to_thread(pointer.move, 60, 60)
                nudged = True
                validation = await driver.validate_pointer(located)
        elapsed = (time.monotonic() - started) * 1000
        # What the rig needs to prove here is that the pointer is *visible*: a visible
        # pointer away from the target is the normal starting state, and the tap stage
        # is what drives it. Only a missing pointer is a rig failure.
        visible = validation.incident != "pointer_missing"
        if validation.ok:
            detail = "pointer verified on target"
        elif visible:
            error = (
                f" {validation.error_px:.0f}px from the target center"
                if validation.error_px is not None
                else ""
            )
            detail = f"pointer visible{error} ({validation.incident}); the tap will drive it"
        else:
            detail = "pointer not visible after a wake nudge (pointer_missing)"
        if nudged:
            detail += " [woken by a 60-unit nudge]"
        report.steps.append(SmokeStep("pointer_precheck", visible, detail, elapsed))
        report.ok = report.ok and visible

    if tap and located is not None:
        started = time.monotonic()
        x, y = session.geometry.normalized_box_center(located.box)
        result = await driver.tap_with_result(x, y)
        elapsed = (time.monotonic() - started) * 1000
        detail = f"screen=({x}, {y})"
        if not result.ok:
            detail += f" incident={result.incident}"
        if result.detail:
            detail += f" {result.detail}"
        report.ok = report.ok and result.ok
        report.steps.append(SmokeStep("tap", result.ok, detail.strip(), elapsed))

    return report
