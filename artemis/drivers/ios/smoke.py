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


async def run_smoke(driver, *, target: str | None = None, tap: bool = True) -> SmokeReport:
    """Runs the rig smoke against ``driver`` (an ``IOSUvcHidDriver``).

    Continues after a failed *optional* stage (OCR, pointer, tap) so one report shows
    every stage; stops at the first failure that makes later ones meaningless (no frame).
    """
    report = SmokeReport(ok=True)
    session = driver.session

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
        frame = await session.snapshot()
    except Exception as exc:  # noqa: BLE001
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

    located = None
    if target:
        started = time.monotonic()
        from artemis.perception.base import Target

        source = session.perception[0]
        try:
            located = await source.locate(frame, Target(target, (0, 0, 0, 0), 0.0, "ocr"))
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
            report.steps.append(SmokeStep("locate", False, f"text {target!r} not found", elapsed))
            return report
        report.steps.append(
            SmokeStep(
                "locate",
                True,
                f"{located.label!r} box={tuple(round(v, 3) for v in located.box)}"
                f" confidence={located.confidence:.2f}",
                elapsed,
            )
        )

        started = time.monotonic()
        validation = await driver.validate_pointer(located)
        elapsed = (time.monotonic() - started) * 1000
        detail = (
            "pointer verified on target"
            if validation.ok
            else (
                f"incident={validation.incident}"
                + (
                    f" error_px={validation.error_px:.1f}"
                    if validation.error_px is not None
                    else ""
                )
            )
        )
        report.steps.append(SmokeStep("pointer_precheck", validation.ok, detail, elapsed))
        # A pointer that is not on the target is exactly what the tap precheck would
        # return; the tap stage below is where the honest answer comes from.
        report.ok = report.ok and validation.ok

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
