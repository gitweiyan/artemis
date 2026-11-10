"""Typed coordinate spaces and the single transform graph (ADR-002).

The legacy 0-1000 agent point and 0-1 target box stay in place; this module
makes the space explicit at every boundary and owns crop, rotation, scale and
desktop window/display offsets. ``ScreenGeometry`` remains the compatibility
facade.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
import math
from typing import Any, Mapping

__all__ = [
    "TRANSFORM_SCHEMA",
    "BoxRef",
    "CoordinateSpace",
    "CoordinateTransform",
    "PointRef",
    "TransformParams",
    "TransformRecord",
    "transform_id",
]


class CoordinateSpace(StrEnum):
    AGENT_POINT = "agent_point"  # 0..1000 integers (prompt/tool schema)
    TARGET_BOX = "target_box"  # 0..1 floats (OCR/vision boxes)
    FRAME_PIXEL = "frame_pixel"  # captured image pixels
    DEVICE_PIXEL = "device_pixel"  # target screen pixels (rotation applied)
    POINTER_FRAME_PIXEL = "pointer_frame_pixel"  # cropped pointer-detector frame
    WINDOW_POINT = "window_point"  # desktop window-local points
    DISPLAY_POINT = "display_point"  # desktop display/global points


TRANSFORM_SCHEMA = "coordinate-transform/v1"
_ROTATIONS = (0, 90, 180, 270)


def _finite(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number, got {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return number


def _positive_pair(value: Any, name: str) -> tuple[int, int]:
    try:
        width, height = int(value[0]), int(value[1])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a (width, height) pair") from exc
    if width <= 0 or height <= 0:
        raise ValueError(f"{name} must be positive, got {value!r}")
    return width, height


def _rect(value: Any, frame_size: tuple[int, int]) -> tuple[int, int, int, int]:
    if value is None:
        return (0, 0, frame_size[0], frame_size[1])
    try:
        left, top, right, bottom = (int(part) for part in value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"content_rect must be four integers, got {value!r}") from exc
    if not (0 <= left < right <= frame_size[0] and 0 <= top < bottom <= frame_size[1]):
        raise ValueError(f"content_rect {value!r} is outside frame {frame_size!r}")
    return left, top, right, bottom


@dataclass(frozen=True)
class PointRef:
    space: CoordinateSpace
    x: float
    y: float
    frame_id: str | None = None
    transform_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "space", CoordinateSpace(self.space))
        object.__setattr__(self, "x", _finite(self.x, "x"))
        object.__setattr__(self, "y", _finite(self.y, "y"))

    def as_tuple(self) -> tuple[float, float]:
        return self.x, self.y


@dataclass(frozen=True)
class BoxRef:
    space: CoordinateSpace
    left: float
    top: float
    right: float
    bottom: float
    frame_id: str | None = None
    transform_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "space", CoordinateSpace(self.space))
        left = _finite(self.left, "left")
        top = _finite(self.top, "top")
        right = _finite(self.right, "right")
        bottom = _finite(self.bottom, "bottom")
        if right < left or bottom < top:
            raise ValueError(f"box is inverted: {(left, top, right, bottom)!r}")
        object.__setattr__(self, "left", left)
        object.__setattr__(self, "top", top)
        object.__setattr__(self, "right", right)
        object.__setattr__(self, "bottom", bottom)


@dataclass(frozen=True)
class TransformParams:
    source: CoordinateSpace
    target: CoordinateSpace
    frame_size: tuple[int, int]
    screen_size: tuple[int, int]
    content_rect: tuple[int, int, int, int] | None = None
    rotation_degrees: int = 0
    scale: tuple[float, float] = (1.0, 1.0)
    window_origin: tuple[float, float] = (0.0, 0.0)
    display_scale: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", CoordinateSpace(self.source))
        object.__setattr__(self, "target", CoordinateSpace(self.target))
        frame_size = _positive_pair(self.frame_size, "frame_size")
        screen_size = _positive_pair(self.screen_size, "screen_size")
        object.__setattr__(self, "frame_size", frame_size)
        object.__setattr__(self, "screen_size", screen_size)
        object.__setattr__(self, "content_rect", _rect(self.content_rect, frame_size))
        rotation = int(self.rotation_degrees) % 360
        if rotation not in _ROTATIONS:
            raise ValueError("rotation_degrees must be one of 0, 90, 180, 270")
        object.__setattr__(self, "rotation_degrees", rotation)
        scale = (_finite(self.scale[0], "scale_x"), _finite(self.scale[1], "scale_y"))
        if scale[0] <= 0 or scale[1] <= 0:
            raise ValueError("scale must be positive")
        object.__setattr__(self, "scale", scale)
        object.__setattr__(
            self,
            "window_origin",
            (
                _finite(self.window_origin[0], "window_x"),
                _finite(self.window_origin[1], "window_y"),
            ),
        )
        display = _finite(self.display_scale, "display_scale")
        if display <= 0:
            raise ValueError("display_scale must be positive")
        object.__setattr__(self, "display_scale", display)

    @property
    def content_size(self) -> tuple[int, int]:
        left, top, right, bottom = self.content_rect or (0, 0, *self.frame_size)
        return right - left, bottom - top

    def canonical_payload(self, *, schema: str = TRANSFORM_SCHEMA) -> dict[str, Any]:
        left, top, right, bottom = self.content_rect or (0, 0, *self.frame_size)
        return {
            "schema": schema,
            "source": self.source.value,
            "target": self.target.value,
            "frame_size": [int(self.frame_size[0]), int(self.frame_size[1])],
            "screen_size": [int(self.screen_size[0]), int(self.screen_size[1])],
            "content_rect": [int(left), int(top), int(right), int(bottom)],
            "rotation_degrees": int(self.rotation_degrees) % 360,
            "scale": [round(float(self.scale[0]), 6), round(float(self.scale[1]), 6)],
            "window_origin": [
                round(float(self.window_origin[0]), 6),
                round(float(self.window_origin[1]), 6),
            ],
            "display_scale": round(float(self.display_scale), 6),
        }


def transform_id(params: TransformParams, *, schema: str = TRANSFORM_SCHEMA) -> str:
    """``xform-<sha256(canonical params + schema)[:24]>``; frame_id is not hashed."""

    payload = params.canonical_payload(schema=schema)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "xform-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class TransformRecord:
    transform_id: str
    params: TransformParams
    schema: str = TRANSFORM_SCHEMA
    #: Human label only; never identity.
    revision: str | None = None

    def __post_init__(self) -> None:
        expected = transform_id(self.params, schema=self.schema)
        if self.transform_id != expected:
            raise ValueError(f"transform_id mismatch: {self.transform_id!r} != {expected!r}")

    def verify(self) -> bool:
        return self.transform_id == transform_id(self.params, schema=self.schema)


def _rotate(u: float, v: float, rotation: int) -> tuple[float, float]:
    if rotation == 0:
        return u, v
    if rotation == 90:
        return 1.0 - v, u
    if rotation == 180:
        return 1.0 - u, 1.0 - v
    return v, 1.0 - u


def _unrotate(u: float, v: float, rotation: int) -> tuple[float, float]:
    if rotation == 0:
        return u, v
    if rotation == 90:
        return v, 1.0 - u
    if rotation == 180:
        return 1.0 - u, 1.0 - v
    return 1.0 - v, u


@dataclass(frozen=True)
class CoordinateTransform:
    record: TransformRecord
    frame_id: str = ""

    @property
    def transform_id(self) -> str:
        return self.record.transform_id

    @property
    def params(self) -> TransformParams:
        return self.record.params

    def _to_device(self, point: PointRef) -> tuple[float, float]:
        params = self.params
        space = point.space
        if space is CoordinateSpace.AGENT_POINT:
            return point.x / 1000.0 * params.screen_size[0], point.y / 1000.0 * params.screen_size[
                1
            ]
        if space is CoordinateSpace.TARGET_BOX:
            return point.x * params.screen_size[0], point.y * params.screen_size[1]
        if space is CoordinateSpace.DEVICE_PIXEL:
            return point.x, point.y
        if space is CoordinateSpace.DISPLAY_POINT:
            return point.x * params.display_scale, point.y * params.display_scale
        if space is CoordinateSpace.WINDOW_POINT:
            return (
                (point.x + params.window_origin[0]) * params.display_scale,
                (point.y + params.window_origin[1]) * params.display_scale,
            )
        if space not in (CoordinateSpace.FRAME_PIXEL, CoordinateSpace.POINTER_FRAME_PIXEL):
            raise ValueError(f"transform cannot consume {space.value!r}")

        left, top = params.content_rect[:2]
        cw, ch = params.content_size
        u, v = (point.x - left) / cw, (point.y - top) / ch
        u, v = _rotate(u, v, params.rotation_degrees)
        return u * cw * params.scale[0], v * ch * params.scale[1]

    def _from_device(self, x: float, y: float, space: CoordinateSpace) -> tuple[float, float]:
        params = self.params
        if space is CoordinateSpace.AGENT_POINT:
            return x / params.screen_size[0] * 1000.0, y / params.screen_size[1] * 1000.0
        if space is CoordinateSpace.TARGET_BOX:
            return x / params.screen_size[0], y / params.screen_size[1]
        if space is CoordinateSpace.DEVICE_PIXEL:
            return x, y
        if space is CoordinateSpace.DISPLAY_POINT:
            return x / params.display_scale, y / params.display_scale
        if space is CoordinateSpace.WINDOW_POINT:
            return (
                x / params.display_scale - params.window_origin[0],
                y / params.display_scale - params.window_origin[1],
            )
        if space not in (CoordinateSpace.FRAME_PIXEL, CoordinateSpace.POINTER_FRAME_PIXEL):
            raise ValueError(f"transform cannot produce {space.value!r}")

        left, top = params.content_rect[:2]
        cw, ch = params.content_size
        u = x / (cw * params.scale[0])
        v = y / (ch * params.scale[1])
        u, v = _unrotate(u, v, params.rotation_degrees)
        return left + u * cw, top + v * ch

    def apply(self, point: PointRef) -> PointRef:
        if point.space is not self.params.source:
            raise ValueError(
                f"point space {point.space.value!r} does not match transform source"
                f" {self.params.source.value!r}"
            )
        device = self._to_device(point)
        x, y = self._from_device(*device, self.params.target)
        return PointRef(
            self.params.target,
            x,
            y,
            frame_id=self.frame_id or point.frame_id,
            transform_id=self.transform_id,
        )

    def invert(self, point: PointRef) -> PointRef:
        if point.space is not self.params.target:
            raise ValueError(
                f"point space {point.space.value!r} does not match transform target"
                f" {self.params.target.value!r}"
            )
        device = self._to_device(PointRef(self.params.target, point.x, point.y))
        x, y = self._from_device(*device, self.params.source)
        return PointRef(
            self.params.source,
            x,
            y,
            frame_id=self.frame_id or point.frame_id,
            transform_id=self.transform_id,
        )

    def apply_box(self, box: BoxRef) -> BoxRef:
        if box.space is not self.params.source:
            raise ValueError(
                f"box space {box.space.value!r} does not match transform source"
                f" {self.params.source.value!r}"
            )
        corners = [
            self.apply(PointRef(self.params.source, x, y))
            for x in (box.left, box.right)
            for y in (box.top, box.bottom)
        ]
        xs = [point.x for point in corners]
        ys = [point.y for point in corners]
        return BoxRef(
            self.params.target,
            min(xs),
            min(ys),
            max(xs),
            max(ys),
            frame_id=self.frame_id or box.frame_id,
            transform_id=self.transform_id,
        )

    @classmethod
    def agent_to_device(
        cls,
        *,
        screen_size: tuple[int, int],
        frame_id: str = "",
    ) -> CoordinateTransform:
        """Agent 0..1000 points → device pixels (screen-normalized)."""

        screen_w, screen_h = _positive_pair(screen_size, "screen_size")
        params = TransformParams(
            source=CoordinateSpace.AGENT_POINT,
            target=CoordinateSpace.DEVICE_PIXEL,
            frame_size=(screen_w, screen_h),
            screen_size=(screen_w, screen_h),
            content_rect=(0, 0, screen_w, screen_h),
        )
        return cls(TransformRecord(transform_id(params), params), frame_id=frame_id)

    @classmethod
    def frame_to_device(
        cls,
        *,
        frame_size: tuple[int, int],
        screen_size: tuple[int, int],
        content_rect: tuple[int, int, int, int] | None = None,
        rotation_degrees: int = 0,
        frame_id: str = "",
        revision: str | None = None,
    ) -> CoordinateTransform:
        frame_w, frame_h = _positive_pair(frame_size, "frame_size")
        screen_w, screen_h = _positive_pair(screen_size, "screen_size")
        rect = _rect(content_rect, (frame_w, frame_h))
        cw, ch = rect[2] - rect[0], rect[3] - rect[1]
        params = TransformParams(
            source=CoordinateSpace.FRAME_PIXEL,
            target=CoordinateSpace.DEVICE_PIXEL,
            frame_size=(frame_w, frame_h),
            screen_size=(screen_w, screen_h),
            content_rect=rect,
            rotation_degrees=rotation_degrees,
            scale=(screen_w / cw, screen_h / ch),
        )
        return cls(
            TransformRecord(transform_id(params), params, revision=revision), frame_id=frame_id
        )

    @classmethod
    def device_to_frame(
        cls,
        *,
        frame_size: tuple[int, int],
        screen_size: tuple[int, int],
        content_rect: tuple[int, int, int, int] | None = None,
        rotation_degrees: int = 0,
        target: CoordinateSpace = CoordinateSpace.FRAME_PIXEL,
        frame_id: str = "",
        revision: str | None = None,
    ) -> CoordinateTransform:
        frame_w, frame_h = _positive_pair(frame_size, "frame_size")
        screen_w, screen_h = _positive_pair(screen_size, "screen_size")
        rect = _rect(content_rect, (frame_w, frame_h))
        cw, ch = rect[2] - rect[0], rect[3] - rect[1]
        params = TransformParams(
            source=CoordinateSpace.DEVICE_PIXEL,
            target=target,
            frame_size=(frame_w, frame_h),
            screen_size=(screen_w, screen_h),
            content_rect=rect,
            rotation_degrees=rotation_degrees,
            scale=(screen_w / cw, screen_h / ch),
        )
        return cls(
            TransformRecord(transform_id(params), params, revision=revision), frame_id=frame_id
        )
