"""Cross-step no-op guard for the Flash reactive loop.

Flash has no Operator-style screen-similarity component: the model sees only
``text_summary`` on the tool result, while the post-action frame arrives later
as the next observation. The driver may already know the action did nothing
(``incident="no_effect"``, a tiny ``crop_change``), and ``data_engine`` already
stamps ``pre/post_image_dhash``, but none of that reaches the next prompt.

The 2026-11-05 milk-tea run pressed BACK four times with identical frames and
the loop never pushed back. This module turns that evidence into bounded,
ephemeral guidance: two consecutive no-ops ask for a strategy change, three
stop the run. Deliberate stillness (waiting/loading) is neither counted nor
allowed to clear a real stagnation.

It is deliberately pure: no context, no device, no I/O, so the guard can be
tested without a run and reused by any other reactive loop.
"""

from __future__ import annotations

from dataclasses import dataclass

from artemis.safety.pixel import DEFAULT_MIN_CHANGED_FRACTION

#: Actions whose stillness is deliberate. A wait must not be mistaken for a
#: failed action, and must not reset a stagnation the model has not resolved.
WAIT_ACTIONS = frozenset(
    {
        "wait",
        "wait_for_text",
        "sleep",
        "take_screenshot",
        "get_ui_elements",
        "ask_explorer",
        "read_note",
        "list_notes",
        "report_task_status",
    }
)

WARN_AFTER = 2
ABORT_AFTER = 3
#: Two 64-bit dHashes of the same JPEG-encoded screen can differ by a bit or
#: two from compression noise; anything larger is a real visual change.
SAME_SCREEN_MAX_DHASH_DISTANCE = 2

_WARN_NOTICE = (
    "NO-OP GUARD: {n} consecutive actions produced no visible change ({reason}). "
    "Do not repeat them. Verify the on-screen state first, then switch strategy: "
    "a different control, a keyboard route (Tab/Space/Escape), or re-launch the app. "
    "Repeating the same tap or key is treated as a stalled run."
)
_ABORT_NOTICE = (
    "NO-OP GUARD: {n} consecutive actions produced no visible change. "
    "Stopping the run instead of spending more turns on the same stalled state."
)

__all__ = [
    "ABORT_AFTER",
    "NoOpTracker",
    "NoOpVerdict",
    "SAME_SCREEN_MAX_DHASH_DISTANCE",
    "WAIT_ACTIONS",
    "WARN_AFTER",
]


@dataclass(frozen=True)
class NoOpVerdict:
    """What one action did to the stagnation counter.

    ``action`` is ``none`` / ``warn`` / ``abort``; ``notice`` is the ephemeral
    text to put on the next observation tail when it is not ``none``.
    """

    is_no_op: bool
    consecutive: int
    action: str
    reason: str = ""
    notice: str | None = None


class NoOpTracker:
    """Bounded accumulator of consecutive actions with no visible effect."""

    def __init__(
        self,
        *,
        warn_after: int = WARN_AFTER,
        abort_after: int = ABORT_AFTER,
        min_crop_change: float = DEFAULT_MIN_CHANGED_FRACTION,
    ):
        self.warn_after = warn_after
        self.abort_after = abort_after
        self.min_crop_change = min_crop_change
        self._consecutive = 0

    @property
    def consecutive(self) -> int:
        return self._consecutive

    def observe(
        self,
        action: str,
        *,
        status: str = "success",
        incident: str | None = None,
        metrics: dict | None = None,
        unchanged: bool | None = None,
    ) -> NoOpVerdict:
        """Fold one action into the counter.

        ``unchanged`` is the pre/post dHash verdict: ``True`` identical,
        ``False`` changed, ``None`` unknown (no frame, no Pillow, no hash).
        """
        if action in WAIT_ACTIONS:
            return NoOpVerdict(False, self._consecutive, "none")

        is_no_op, reason = self._classify(status, incident, metrics, unchanged)
        if not is_no_op:
            self._consecutive = 0
            return NoOpVerdict(False, 0, "none")

        self._consecutive += 1
        if self._consecutive >= self.abort_after:
            return NoOpVerdict(
                True,
                self._consecutive,
                "abort",
                reason,
                _ABORT_NOTICE.format(n=self._consecutive),
            )
        if self._consecutive >= self.warn_after:
            return NoOpVerdict(
                True,
                self._consecutive,
                "warn",
                reason,
                _WARN_NOTICE.format(n=self._consecutive, reason=reason or "unknown"),
            )
        return NoOpVerdict(True, self._consecutive, "none", reason)

    def _classify(
        self,
        status: str,
        incident: str | None,
        metrics: dict | None,
        unchanged: bool | None,
    ) -> tuple[bool, str]:
        # The driver's own verdict is authoritative.
        if incident == "no_effect":
            return True, "incident=no_effect"
        # A refused/failed action (e.g. iOS BACK) advances nothing either, even
        # when a buggy actuator once reported it as success.
        if status != "success":
            return True, "action_failed"
        if unchanged is True:
            return True, "screen_unchanged"
        # crop_change is a *target-region* signal only: a sheet may open away
        # from the target (verify_effect's OR rule). Use it as a no-op only when
        # the whole-frame hash is unavailable and never against a changed frame.
        crop = metrics.get("crop_change") if isinstance(metrics, dict) else None
        if (
            isinstance(crop, (int, float))
            and not isinstance(crop, bool)
            and crop < self.min_crop_change
            and unchanged is None
        ):
            return True, "crop_unchanged"
        return False, ""
