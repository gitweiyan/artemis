"""Missing confidence must not be invented as a high-confidence failure (ADR-007/§7)."""

from __future__ import annotations

import asyncio
import base64
from unittest.mock import AsyncMock, patch

import pytest

from artemis.agents.validator.categories import ValidationErrorCategory


def _session():
    class Session:
        async def screenshot_b64(self):
            return base64.b64encode(b"live").decode()

    return Session()


def _run(verdict):
    from artemis.agents.validator import precondition_pixel

    action = {"action": "tap", "coordinates": [100, 200], "target_description": "button"}
    with (
        patch.object(
            precondition_pixel.visualization,
            "crop_and_annotate_target",
            return_value=b"crop",
        ),
        patch.object(
            precondition_pixel,
            "acomplete_structured",
            AsyncMock(return_value=verdict),
        ),
    ):
        return asyncio.run(
            precondition_pixel._run_attempt(
                _session(), object(), "prompt", b"orig", [100, 200], action, None, 1
            )
        )


def test_missing_confidence_bypasses_instead_of_claiming_a_low_or_high_failure():
    result = _run({"is_present": False, "reasoning": "cannot tell"})

    assert result is not None
    assert result[0] is True
    assert result[1] is ValidationErrorCategory.PIXEL_BYPASSED


def test_numeric_high_confidence_still_blocks_an_absent_target():
    result = _run({"is_present": False, "reasoning": "gone", "confidence": 0.9})

    assert result is not None
    assert result[0] is False
    assert result[1] is ValidationErrorCategory.PIXEL_TARGET_DISAPPEARED
