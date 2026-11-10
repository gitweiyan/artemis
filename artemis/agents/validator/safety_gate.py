"""Validator adapter for the task-mode ActionGate port (ADR-007 / Phase 5).

The XML-first + pixel-fallback policy remains here, where it can call the
Validator's existing patchable seams. ``execution_loop`` only sees a
``SafetyRequest``/``GateDecision``; it does not own the policy.
"""

from __future__ import annotations

from typing import Any

from artemis.agents.validator.categories import ValidationErrorCategory
from artemis.safety.gate import GateDecision, SafetyRequest
from artemis.safety.incidents import canonical_incident
from third_party.mobile_use.utils.logger import get_logger

logger = get_logger(__name__)

__all__ = ["ValidatorActionGate"]


class ValidatorActionGate:
    """The Validator's XML/pixel precondition policy behind the common port."""

    def __init__(self, node: Any) -> None:
        self.node = node

    async def check(self, request: SafetyRequest) -> GateDecision:
        meta = request.metadata
        node = self.node
        session = meta["session"]
        action_item = meta["action_item"]
        state = meta.get("state")
        pre_screenshot_b64 = meta.get("pre_screenshot_b64", "")
        original_coords = list(meta.get("original_coords") or [])

        is_explorer_candidate = action_item.get("target_class") == "ExplorerCandidate"
        is_ocr_element = "[OCR]" in str(action_item.get("target_class") or "")
        has_index_metadata = bool(
            (
                action_item.get("target_text")
                or action_item.get("target_bounds")
                or action_item.get("target_resource_id")
            )
            and not is_explorer_candidate
            and not is_ocr_element
        )

        async def pixel_check() -> tuple[bool, ValidationErrorCategory, str]:
            return await node._validate_action_precondition_pixel(
                session,
                action_item,
                pre_screenshot_b64,
                original_coords,
                state=state,
            )

        if not has_index_metadata:
            passed, category, error = await pixel_check()
        else:
            passed, category, error = await node._validate_action_precondition(
                session, action_item, state=state
            )
            if not passed:
                if category == ValidationErrorCategory.XML_BYPASSED:
                    logger.info(
                        f"Pre-execution XML-based validation bypassed ({error})."
                        " Falling back to Pixel-based validation..."
                    )
                    passed, category, error = await pixel_check()
                else:
                    logger.info(
                        f"Pre-execution XML-based validation failed: {error}."
                        " Attempting Pixel-based validation fallback..."
                    )
                    pixel_passed, pixel_category, _pixel_error = await pixel_check()
                    if pixel_passed and pixel_category == ValidationErrorCategory.NONE:
                        logger.success(
                            "Pixel validation confirmed the target after XML validation failed."
                        )
                        passed, category, error = True, ValidationErrorCategory.NONE, ""

        incident = None if passed else canonical_incident(category)
        return GateDecision(
            allowed=passed,
            incident=incident,
            reason=error,
            evidence={"category": category.value, "validator": "xml+pixel"},
            requires_post_verification=passed,
        )

    async def verify(self, request: SafetyRequest, result: Any) -> GateDecision:
        """Task-level post-action verification is still owned by the Checker."""

        return GateDecision(
            allowed=True,
            reason="post-action verification is delegated to the task Checker",
            requires_post_verification=False,
        )
