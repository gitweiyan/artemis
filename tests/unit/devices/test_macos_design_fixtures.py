"""Design-only macOS profile fixtures (Phase 7; no production adapter)."""

from __future__ import annotations

import json
from pathlib import Path

from artemis.core.capabilities import (
    CATALOG,
    CapabilityDeclaration,
    CapabilityId,
    CapabilityManifest,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "macos"


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _display_point_to_global(display, local):
    return [
        display["origin_points"][0] + local[0],
        display["origin_points"][1] + local[1],
    ]


def _display_point_to_screenshot(display, local):
    if display["rotation_degrees"] in (0, 180):
        return [round(x * display["backing_scale"]) for x in local]
    return [
        round(local[1] * display["backing_scale"]),
        round(local[0] * display["backing_scale"]),
    ]


def test_profile_capabilities_are_in_the_closed_catalog():
    required = {
        CapabilityId.FRAME_CAPTURE,
        CapabilityId.UI_TREE_READ,
        CapabilityId.WINDOW_INSPECT,
        CapabilityId.WINDOW_FOCUS,
        CapabilityId.POINTER_ABSOLUTE,
        CapabilityId.KEYBOARD_KEY,
        CapabilityId.TEXT_INPUT,
    }
    optional = {CapabilityId.OCR_READ, CapabilityId.GROUNDING_LOCATE, CapabilityId.DEVICE_HEALTH}
    manifest = CapabilityManifest(
        "macos.desktop",
        "0-design",
        tuple(CapabilityDeclaration(item) for item in (*required, *optional)),
    )

    assert set(manifest.declared_ids) <= set(CATALOG)
    assert required <= {CapabilityId(item) for item in manifest.declared_ids}


def test_multi_display_origins_and_retina_scale_are_explicit():
    displays = {item["display_id"]: item for item in _load("displays.json")}

    assert _display_point_to_global(displays["studio-secondary"], [10, 20]) == [1522, 20]
    assert _display_point_to_global(displays["left-negative"], [10, 20]) == [-1070, 20]
    assert _display_point_to_screenshot(displays["builtin-retina"], [10, 20]) == [20, 40]
    assert _display_point_to_screenshot(displays["left-negative"], [10, 20]) == [20, 10]


def test_ax_fixture_binds_window_display_and_focus():
    tree = _load("ax_tree.json")
    displays = {item["display_id"] for item in _load("displays.json")}
    nodes = {node["id"]: node for node in tree["nodes"]}

    assert tree["display_id"] in displays
    assert tree["window_id"] == "window-settings"
    assert nodes[tree["focused_element_id"]]["focused"] is True
    assert nodes["ax-wifi"]["frame"][0] >= tree["frame_points"][0]


def test_input_fixture_requires_release_and_unicode_text():
    events = _load("input_events.json")
    clicks = [event for event in events["events"] if event["kind"] == "mouse.click"]

    assert clicks[0]["space"] == "HOST_LOGICAL"
    assert events["release_required"] == ["keyboard.key"]
    assert any(event.get("text") == "设置" for event in events["events"])


def test_permission_matrix_never_treats_unknown_as_available():
    permissions = _load("permissions.json")

    assert permissions["screen_recording"]["required"] is True
    assert permissions["accessibility"]["required"] is True
    assert permissions["input_monitoring"]["status"] == "unknown"
    assert all(
        item["status"] in {"available", "unavailable", "unknown"} for item in permissions.values()
    )
    assert "Screen Recording" in permissions["screen_recording"]["remediation"]
