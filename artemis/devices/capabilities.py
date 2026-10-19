"""Platform-neutral capability declarations.

A capability describes what a source or sink can actually do. Core orchestration
must branch on these flags, never on a platform name.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CapabilitySet:
    supports_ui_tree: bool = False
    supports_absolute_tap: bool = False
    supports_relative_pointer: bool = False
    requires_pointer_precheck: bool = False
    supports_ocr: bool = False
    supports_text_input: bool = False
    supports_shell: bool = False
