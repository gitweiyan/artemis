"""Validator adapter for the task-mode ActionGate port (ADR-007 / Phase 5).

The XML-first + pixel-fallback policy remains here, where it can call the
Validator's existing patchable seams. ``execution_loop`` only sees a
``SafetyRequest``/``GateDecision``; it does not own the policy.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any

from artemis.agents.validator.categories import ValidationErrorCategory
from artemis.safety.gate import GateDecision, SafetyRequest
from artemis.safety.evidence import evidence_policy
from artemis.safety.incidents import canonical_incident
from artemis.safety.pixel import verify_effect
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
        policy = evidence_policy(
            request.action,
            absolute_touch=bool(meta.get("absolute_touch", False)),
            relative_pointer=bool(meta.get("relative_pointer", True)),
            remote_input=bool(meta.get("remote_input", False)),
        )
        return GateDecision(
            allowed=passed,
            incident=incident,
            reason=error,
            evidence={
                "category": category.value,
                "validator": "xml+pixel",
                "evidence_policy": policy.as_dict(),
            },
            requires_post_verification=bool(passed and policy.requires_post_verification),
        )

    async def verify(self, request: SafetyRequest, result: Any) -> GateDecision:
        """Task-level post-action pixel probe for an explicitly gated action.

        The caller supplies the before/after screenshots and the target point in
        screen pixels via ``request.metadata``. Missing or undecodable evidence is
        ``target_unverified``/``decode_error`` — never a silent success.
        """

        meta = request.metadata
        before_b64 = meta.get("before_image_b64")
        after_b64 = meta.get("after_image_b64")
        point = meta.get("point_px")
        if not before_b64 or not after_b64 or not point:
            return GateDecision(
                False,
                "target_unverified",
                "post-action verification needs before/after screenshots and a target point",
                requires_post_verification=False,
            )
        try:
            before = base64.b64decode(before_b64)
            after = base64.b64decode(after_b64)
            x, y = int(point[0]), int(point[1])
            verdict = verify_effect(before, after, (x, y))
        except (ValueError, TypeError, OSError, binascii.Error):
            return GateDecision(
                False,
                "decode_error",
                "post-action verification could not decode its evidence",
                requires_post_verification=False,
            )
        evidence = {
            "crop_change": verdict.crop_change,
            "frame_change": verdict.frame_change,
            "result_ok": bool(getattr(result, "ok", result)),
        }
        if verdict.ok:
            return GateDecision(
                True,
                None,
                "post-action screen evidence changed",
                evidence=evidence,
                requires_post_verification=False,
            )
        return GateDecision(
            False,
            verdict.incident or "no_effect",
            "post-action screen evidence did not change",
            evidence=evidence,
            requires_post_verification=False,
        )
