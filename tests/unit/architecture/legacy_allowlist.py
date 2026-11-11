"""Explicit migration debt for the Phase 9 architecture ratchet.

Every entry must name an owner, an exit condition and the phase after which it
must be removed. The test suite verifies that current imports are registered,
new imports fail, and stale/expired entries are removed rather than being kept
forever.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["COMPLETED_PHASES", "LEGACY_IMPORTS", "LegacyImport"]


@dataclass(frozen=True)
class LegacyImport:
    importer: str
    imported: str
    owner: str
    exit_condition: str
    review_after: str

    def __post_init__(self) -> None:
        for name in ("importer", "imported", "owner", "exit_condition", "review_after"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} must not be empty")


#: Phases whose exit criteria are already considered closed. An allowlist entry
#: with one of these as review_after is expired and must be removed/migrated.
COMPLETED_PHASES = frozenset({"Phase 0", "Phase 1", "Phase 2"})


def _entry(importer, imported, review_after="Phase 4", owner="Phase 3R/4"):
    return LegacyImport(
        importer=importer,
        imported=imported,
        owner=owner,
        exit_condition=(
            "Remove after the Session-backed Port/Facade migration and the"
            " corresponding real-device regression"
        ),
        review_after=review_after,
    )


LEGACY_IMPORTS = (
    # agents → controller compatibility facade
    # graph fallback observation
    # L4 builders still use legacy capture/selector and the iOS Reflex helpers
    # Device profile still imports Reflex pointer/locator internals
    # L3 source/sink code imports compatibility capability/session DTOs
    _entry("artemis/perception/app_automation.py", "artemis.devices.capabilities", "Phase 5"),
    _entry("artemis/perception/base.py", "artemis.devices.capabilities", "Phase 5"),
    _entry("artemis/perception/camera.py", "artemis.devices.capabilities", "Phase 5"),
    _entry("artemis/perception/composite.py", "artemis.devices.capabilities", "Phase 5"),
    _entry("artemis/perception/composite.py", "artemis.devices.session", "Phase 5"),
    _entry("artemis/perception/uvc.py", "artemis.devices.capabilities", "Phase 5"),
    _entry("artemis/control/app_control.py", "artemis.devices.capabilities", "Phase 5"),
    _entry("artemis/control/base.py", "artemis.devices.capabilities", "Phase 5"),
    _entry("artemis/control/composite.py", "artemis.devices.capabilities", "Phase 5"),
    _entry("artemis/control/hid.py", "artemis.devices.capabilities", "Phase 5"),
)
