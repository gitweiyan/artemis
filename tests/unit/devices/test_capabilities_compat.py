"""CapabilitySet ↔ catalog compatibility (ADR-001 D6)."""

from __future__ import annotations

from dataclasses import fields

from artemis.core.capabilities import (
    CapabilityId,
    CapabilityRecord,
    CapabilityState,
    CapabilityView,
)
from artemis.devices.capabilities import CapabilitySet
from artemis.devices.capabilities_compat import (
    CAPABILITY_TO_FLAG,
    FLAG_TO_CAPABILITY,
    capability_set_from_view,
    declared_capability_ids,
    manifest_from_capability_set,
)


def test_every_capability_set_flag_maps_to_one_catalog_id():
    flags = {item.name for item in fields(CapabilitySet)}
    assert set(FLAG_TO_CAPABILITY) == flags - {"requires_pointer_precheck"}
    assert len(CAPABILITY_TO_FLAG) == len(FLAG_TO_CAPABILITY)
    assert "requires_pointer_precheck" not in CAPABILITY_TO_FLAG.values()


def test_declared_ids_follow_the_flags():
    caps = CapabilitySet(
        supports_ocr=True, supports_relative_pointer=True, supports_app_launch=False
    )

    assert set(declared_capability_ids(caps)) == {
        CapabilityId.OCR_READ,
        CapabilityId.POINTER_RELATIVE,
    }


def test_manifest_from_capability_set_is_declaration_only():
    manifest = manifest_from_capability_set(
        CapabilitySet(supports_ui_tree=True, supports_absolute_tap=True, supports_app_launch=False),
        adapter_id="android_helper",
        adapter_version="1",
    )

    assert manifest.declared_ids == (CapabilityId.UI_TREE_READ, CapabilityId.TOUCH_ABSOLUTE)


def test_view_to_capability_set_respects_planning_and_execution():
    view = CapabilityView(
        [
            CapabilityRecord(CapabilityId.UI_TREE_READ, CapabilityState.DECLARED, "manifest:a"),
            CapabilityRecord(CapabilityId.POINTER_RELATIVE, CapabilityState.VERIFIED, "evidence:e"),
        ]
    )

    planning = capability_set_from_view(view)
    execution = capability_set_from_view(view, for_execution=True)

    assert planning.supports_ui_tree is True
    assert planning.supports_relative_pointer is True
    assert planning.requires_pointer_precheck is True
    assert execution.supports_ui_tree is False
    assert execution.supports_relative_pointer is True


def test_an_unmapped_default_does_not_become_true():
    caps = capability_set_from_view(CapabilityView([]))

    assert caps.supports_app_launch is False
    assert caps.supports_ocr is False
