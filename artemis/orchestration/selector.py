"""Compatibility re-export: device composition now lives in L4.

ADR-005 moved the pure selector to :mod:`artemis.devices.composition`. Existing
imports (``DeviceProfile``/``select_composition``) keep working.
"""

from __future__ import annotations

from artemis.devices.composition import (
    CAPTURE_MODES,
    Composition,
    CompositionError,
    CompositionRequest,
    select_composition,
)

#: Backwards-compatible name for the pre-selection request.
DeviceProfile = CompositionRequest

__all__ = [
    "CAPTURE_MODES",
    "Composition",
    "CompositionError",
    "CompositionRequest",
    "DeviceProfile",
    "select_composition",
]
