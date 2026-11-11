"""L4 video-segment access for task agents.

Agents request a segment through this module; the legacy controller construction
stays behind the device boundary instead of being imported by L2.
"""

from __future__ import annotations

from typing import Any

from artemis.controllers.controller_factory import get_controller

__all__ = ["get_controller"]


def video_controller(ctx: Any):
    return get_controller(ctx)
