"""Screen observation boundary for L2/L3 callers.

Graph/Flash/Validator consume this module instead of importing the MCP
observation implementation. A real DeviceSession is authoritative; the
controller fallback remains an explicit compatibility path for callers that
have no Session yet. The MCP module is now a re-export shim.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
import inspect
from pathlib import Path
import time
from typing import Any, Protocol, runtime_checkable

from artemis.config import get_temp_dir
from artemis.core.action_types import ActionCode, ObserveResult
from artemis.utils.ocr_xml_fusion import fuse_ocr_with_xml
from artemis.utils.visualization import format_minimal_list_with_elements
from third_party.mobile_use.utils.logger import get_logger

logger = get_logger(__name__)

__all__ = [
    "ControllerObservationPort",
    "ObservationEvidence",
    "ObservationPort",
    "ScreenData",
    "SessionObservationPort",
    "get_screen_data",
    "observe",
    "session_screen_data",
]


@dataclass(frozen=True)
class ObservationEvidence:
    """Identity and freshness of the frame an observation was built from."""

    frame_id: str | None
    captured_at: float | None
    source_id: str
    geometry_revision: str | None
    health: str
    age_s: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "frame_id": self.frame_id,
            "captured_at": self.captured_at,
            "source_id": self.source_id,
            "geometry_revision": self.geometry_revision,
            "health": self.health,
            "age_s": self.age_s,
        }


@dataclass(frozen=True)
class ScreenData:
    base64: str
    elements: list[dict[str, Any]] = field(default_factory=list)
    width: int = 1080
    height: int = 2400
    platform: str = "unknown"
    evidence: ObservationEvidence | None = None


def _evidence_for_frame(frame: Any, source: Any, session: Any) -> ObservationEvidence:
    captured_at = getattr(frame, "captured_at", None)
    try:
        captured_at = float(captured_at) if captured_at is not None else None
    except (TypeError, ValueError):
        captured_at = None
    age_s = None
    if captured_at is not None:
        age_s = max(0.0, time.time() - captured_at)
    source_id = str(getattr(source, "name", None) or getattr(frame, "source", None) or "unknown")
    return ObservationEvidence(
        frame_id=getattr(frame, "frame_id", None) or getattr(session, "last_frame_id", None),
        captured_at=captured_at,
        source_id=source_id,
        geometry_revision=getattr(frame, "geometry_revision", None),
        health=str(getattr(frame, "health", "ok") or "unknown"),
        age_s=age_s,
    )


def _coerce(data: Any, *, evidence: ObservationEvidence | None = None) -> ScreenData:
    raw_elements = getattr(data, "elements", None) or getattr(data, "ui_elements", []) or []
    # Some legacy clients return duck-typed mocks or XML strings here; only a
    # real sequence is an element list. Do not crash a whole turn on a mock.
    elements = list(raw_elements) if isinstance(raw_elements, (list, tuple)) else []
    return ScreenData(
        base64=str(getattr(data, "base64", None) or getattr(data, "screenshot_base64", "") or ""),
        elements=elements,
        width=int(getattr(data, "width", 1080) or 1080),
        height=int(getattr(data, "height", 2400) or 2400),
        platform=str(getattr(data, "platform", "unknown") or "unknown"),
        evidence=evidence or getattr(data, "evidence", None),
    )


async def session_screen_data(ctx: Any) -> ScreenData | None:
    """Read screen data through the Session, including its health/state guard."""

    session = getattr(ctx, "device_session", None)
    if session is None or not isinstance(getattr(session, "perception", None), (list, tuple)):
        return None
    frame = await session.snapshot()
    source = session.perception[0] if session.perception else None
    evidence = _evidence_for_frame(frame, source, session)
    for source in session.perception:
        cached = getattr(source, "_last_data", None)
        if cached is not None:
            return _coerce(cached, evidence=evidence)
    elements: list[dict[str, Any]] = []
    if source is not None:
        tree = await source.ui_tree()
        for item in tree or []:
            bounds = getattr(item, "bounds", None)
            if bounds and len(bounds) == 4:
                left, top, right, bottom = (int(value) for value in bounds)
                elements.append(
                    {
                        "text": getattr(item, "text", ""),
                        "resource-id": getattr(item, "resource_id", ""),
                        "bounds": f"[{left},{top}][{right},{bottom}]",
                        "center": [(left + right) // 2, (top + bottom) // 2],
                    }
                )
    return ScreenData(
        base64=base64.b64encode(frame.image).decode("ascii"),
        elements=elements,
        width=int(frame.width or 1080),
        height=int(frame.height or 2400),
        platform=evidence.source_id,
        evidence=evidence,
    )


async def get_screen_data(ctx: Any, controller: Any | None = None) -> ScreenData:
    """Session first; controller is only used when no usable Session exists."""

    data = await session_screen_data(ctx)
    if data is not None:
        return data
    if controller is None:
        # Explicit injection is preferred; context-attached compatibility
        # controllers are accepted without importing their classes here.
        controller = getattr(ctx, "controller", None)
    if controller is None:
        controller = getattr(ctx, "ui_adb_client", None)
    if controller is None:
        raise RuntimeError(
            "no observation source: a DeviceSession or an explicit controller is required"
        )
    result = controller.get_screen_data()
    if inspect.isawaitable(result):
        result = await result
    return _coerce(result)


@runtime_checkable
class ObservationPort(Protocol):
    """Minimal observation surface consumed by the agent tool layer."""

    async def observe(
        self, settle_ms: int = 400, ocr_results: list | None = None
    ) -> tuple[ObserveResult, bytes | None]: ...


class SessionObservationPort:
    """Observation through DeviceSession, with the controller path as fallback."""

    def __init__(self, ctx: Any, controller: Any | None = None) -> None:
        self.ctx = ctx
        self.controller = controller

    async def observe(
        self, settle_ms: int = 400, ocr_results: list | None = None
    ) -> tuple[ObserveResult, bytes | None]:
        return await observe(self.ctx, self.controller, settle_ms, ocr_results)


class ControllerObservationPort:
    """Compatibility adapter: a controller with ``get_screen_data`` as an Observation Port."""

    def __init__(self, ctx: Any, controller: Any) -> None:
        self.ctx = ctx
        self.controller = controller

    async def observe(
        self, settle_ms: int = 400, ocr_results: list | None = None
    ) -> tuple[ObserveResult, bytes | None]:
        return await observe(self.ctx, self.controller, settle_ms, ocr_results)


async def observe(
    ctx: Any,
    controller: Any | None,
    settle_ms: int = 400,
    ocr_results: list | None = None,
) -> tuple[ObserveResult, bytes | None]:
    """Captures and indexes the current screen.

    Returns the structured observation plus the raw screenshot bytes (an in-process
    convenience; the serialized ``ObserveResult`` carries only the persisted path).
    A failed capture yields ``ok=False``; a failed hierarchy parse still returns the
    screenshot with ``hierarchy_ok=False`` and keeps the Session evidence.
    """
    evidence: ObservationEvidence | None = None
    try:
        if settle_ms > 0:
            logger.info(f"Delaying {settle_ms / 1000:.1f}s before capturing screen state...")
            await asyncio.sleep(settle_ms / 1000.0)
        else:
            logger.info("Retrieving screen data directly (skip settling)...")
        device_data = await get_screen_data(ctx, controller)
        evidence = getattr(device_data, "evidence", None)
        screenshot_bytes = base64.b64decode(
            getattr(device_data, "base64", None) or getattr(device_data, "screenshot_base64", "")
        )
    except Exception as e:
        logger.error(f"Failed to capture screen state: {e}")
        return (
            ObserveResult(
                ok=False,
                code=ActionCode.DEVICE_ERROR,
                message=f"Failed to capture screen state: {e}",
                hierarchy_ok=False,
            ),
            None,
        )

    xml_hierarchy = None
    ocr_results = ocr_results or []
    elements: list[dict] = []
    hierarchy_ok = True
    width = height = None
    try:
        xml_hierarchy = getattr(device_data, "elements", None)
        if xml_hierarchy is None:
            xml_hierarchy = getattr(device_data, "ui_elements", []) or []
        width = device_data.width or 1080
        height = device_data.height or 2400
        if ctx.device:
            ctx.device.device_width = width
            ctx.device.device_height = height

        fused_xml = fuse_ocr_with_xml(xml_hierarchy, ocr_results)
        minimal_list_str, elements, _ = format_minimal_list_with_elements(fused_xml, width, height)
    except Exception as e:
        logger.error(f"Failed to fetch or fuse UI hierarchy: {e}")
        minimal_list_str = "Not available due to hierarchy error."
        hierarchy_ok = False

    screenshot_path = None
    if ctx.data_engine:
        try:
            image_name = ctx.data_engine.get_or_create_image(
                screenshot_bytes,
                ui_tree=xml_hierarchy,
                ocr_result=ocr_results,
            )
            screenshot_path = str(ctx.data_engine.get_image_path(image_name))
        except Exception as e:
            logger.error(f"Failed to save image in DataEngine: {e}")

    if not screenshot_path:
        try:
            temp_dir = get_temp_dir("screenshots")
            temp_file = temp_dir / f"screenshot_{int(time.time())}.jpg"
            temp_file.write_bytes(screenshot_bytes)
            screenshot_path = str(temp_file)
        except Exception as e:
            logger.error(f"Failed to save fallback screenshot: {e}")

    return (
        ObserveResult(
            ok=True,
            message="Screen observed.",
            screenshot_path=screenshot_path,
            image_name=Path(screenshot_path).stem if screenshot_path else None,
            width=width,
            height=height,
            elements_text=minimal_list_str,
            elements=elements,
            hierarchy_ok=hierarchy_ok,
        ),
        screenshot_bytes,
    )
