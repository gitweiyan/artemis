"""Compatibility shim: LocateAnything moved to perception.locator (Phase 9)."""

from __future__ import annotations

from artemis.perception.locate_anything import (  # noqa: F401
    DEFAULT_LOCATE_PROMPT,
    DEFAULT_LOCATE_URL,
    LocateAnythingDetector,
    warm_up_locate_anything,
)

__all__ = [
    "DEFAULT_LOCATE_PROMPT",
    "DEFAULT_LOCATE_URL",
    "LocateAnythingDetector",
    "warm_up_locate_anything",
]
