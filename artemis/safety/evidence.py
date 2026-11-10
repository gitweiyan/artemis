"""Evidence requirements by semantic action (Phase 5).

The same action does not need the same proof on every device: absolute touch
does not need a pointer precheck, a relative-pointer click does, and a remote
key needs neither pointer nor pixel evidence. This module is policy data only;
the ActionGate/Checker decide when to execute it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "ActionEvidencePolicy",
    "EvidenceKind",
    "evidence_policy",
]


class EvidenceKind(StrEnum):
    SCREEN_CHANGE = "screen_change"
    UI_TREE_CHANGE = "ui_tree_change"
    FOCUS_CHANGE = "focus_change"
    POINTER_ALIGNMENT = "pointer_alignment"
    DISPATCH_RECEIPT = "dispatch_receipt"


@dataclass(frozen=True)
class ActionEvidencePolicy:
    action: str
    requires_precondition: bool
    requires_post_verification: bool
    evidence: tuple[EvidenceKind, ...]
    pointer_precheck: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "requires_precondition": self.requires_precondition,
            "requires_post_verification": self.requires_post_verification,
            "evidence": [item.value for item in self.evidence],
            "pointer_precheck": self.pointer_precheck,
        }


_SCREEN = (EvidenceKind.SCREEN_CHANGE,)
_POINTER = (EvidenceKind.POINTER_ALIGNMENT, EvidenceKind.SCREEN_CHANGE)

#: Conservative defaults for a touch/mouse device. The caller can refine with
#: capability metadata; absent evidence never becomes a silent success.
_DEFAULTS = {
    "tap": (True, True, _POINTER, True),
    "click": (True, True, _POINTER, True),
    "click_coordinate": (True, True, _SCREEN, False),
    "long_press": (True, True, _POINTER, True),
    "long_press_on": (True, True, _POINTER, True),
    "swipe": (True, True, _SCREEN, False),
    "input_text": (True, False, _SCREEN, False),
    "focus_and_input_text": (True, False, _SCREEN, False),
    "press_key": (False, False, (EvidenceKind.DISPATCH_RECEIPT,), False),
    "manage_app": (False, True, _SCREEN, False),
    "launch_app": (False, True, _SCREEN, False),
    "open_link": (False, True, _SCREEN, False),
    "wait_for_delay": (False, False, (), False),
}


def evidence_policy(
    action: str,
    *,
    absolute_touch: bool = False,
    relative_pointer: bool = True,
    remote_input: bool = False,
) -> ActionEvidencePolicy:
    """Evidence required for ``action`` under the caller's device capabilities."""

    name = str(action or "").strip()
    row = _DEFAULTS.get(name, (True, False, (EvidenceKind.DISPATCH_RECEIPT,), False))
    pre, post, evidence, pointer = row
    if remote_input:
        pointer = False
        evidence = tuple(item for item in evidence if item is not EvidenceKind.POINTER_ALIGNMENT)
        evidence = evidence + (EvidenceKind.DISPATCH_RECEIPT,)
    elif absolute_touch and name in {"tap", "click", "long_press"}:
        pointer = False
        evidence = tuple(item for item in evidence if item is not EvidenceKind.POINTER_ALIGNMENT)
        if EvidenceKind.SCREEN_CHANGE not in evidence:
            evidence = evidence + _SCREEN
    elif not relative_pointer:
        pointer = False
    return ActionEvidencePolicy(
        action=name,
        requires_precondition=bool(pre),
        requires_post_verification=bool(post),
        evidence=tuple(dict.fromkeys(evidence)),
        pointer_precheck=bool(pointer),
    )
