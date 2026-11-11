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
    # Phase 5 L3→devices DTO migration is complete; keep the mechanism for
    # the next contract boundary rather than deleting the ratchet.
)
