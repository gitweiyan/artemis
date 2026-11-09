"""The single compatibility bridge between CapabilitySet and the catalog.

ADR-001 §D6: callers must not hand-write flag/ID mappings. This module is the
one place that knows both models. The legacy ``CapabilitySet`` keeps its
"declared" meaning; execution decisions should use ``CapabilityView.supports(
..., for_execution=True)``.
"""

from __future__ import annotations

from dataclasses import fields
from typing import Mapping

from artemis.core.capabilities import (
    CapabilityDeclaration,
    CapabilityId,
    CapabilityManifest,
    CapabilityView,
)
from artemis.devices.capabilities import CapabilitySet

__all__ = [
    "CAPABILITY_TO_FLAG",
    "FLAG_TO_CAPABILITY",
    "capability_set_from_view",
    "declared_capability_ids",
    "manifest_from_capability_set",
]

FLAG_TO_CAPABILITY: Mapping[str, str] = {
    "supports_ui_tree": CapabilityId.UI_TREE_READ,
    "supports_absolute_tap": CapabilityId.TOUCH_ABSOLUTE,
    "supports_relative_pointer": CapabilityId.POINTER_RELATIVE,
    "supports_ocr": CapabilityId.OCR_READ,
    "supports_text_input": CapabilityId.TEXT_INPUT,
    "supports_shell": CapabilityId.SHELL_EXEC,
    "supports_app_launch": CapabilityId.APP_LAUNCH,
}
CAPABILITY_TO_FLAG: Mapping[str, str] = {value: key for key, value in FLAG_TO_CAPABILITY.items()}


def declared_capability_ids(capabilities: CapabilitySet) -> tuple[str, ...]:
    return tuple(
        capability
        for flag, capability in FLAG_TO_CAPABILITY.items()
        if bool(getattr(capabilities, flag, False))
    )


def manifest_from_capability_set(
    capabilities: CapabilitySet,
    *,
    adapter_id: str,
    adapter_version: str = "unknown",
) -> CapabilityManifest:
    return CapabilityManifest(
        adapter_id=adapter_id,
        adapter_version=adapter_version,
        declarations=tuple(
            CapabilityDeclaration(capability_id=capability)
            for capability in declared_capability_ids(capabilities)
        ),
    )


def capability_set_from_view(
    view: CapabilityView,
    *,
    for_execution: bool = False,
) -> CapabilitySet:
    values: dict[str, bool] = {}
    for flag in (item.name for item in fields(CapabilitySet)):
        capability = FLAG_TO_CAPABILITY.get(flag)
        values[flag] = bool(capability and view.supports(capability, for_execution=for_execution))
    # pointer-precheck is a derived policy, not a catalog capability (ADR-001 §D1).
    values["requires_pointer_precheck"] = bool(
        view.supports(CapabilityId.POINTER_RELATIVE, for_execution=for_execution)
    )
    return CapabilitySet(**values)
