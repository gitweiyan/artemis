"""Named perception failures, so a bad picture is not just "capture failed".

The execution plan's Phase 1 requirement is explicit: a black frame must return
``uvc_no_signal``, not a "successful screenshot". Three different facts hide behind
"the frame is unusable", and each one has a different fix:

- ``uvc_no_signal`` — a frame arrived and is dark: the card sees no picture
  (HDMI/adapter/HDCP), or the phone's screen is off/locked;
- ``capture_timeout`` — no frame arrived at all: the capture process never produced one,
  the device is gone, or another process holds the card;
- ``uvc_decode_error`` — bytes arrived that are not a decodable image (a torn chunk).

Keeping them apart is what lets the Validator open an incident the Operator can act on
instead of a generic error, and what lets telemetry separate "the rig is dark" from
"the rig is stuck".
"""

from __future__ import annotations

__all__ = [
    "CAPTURE_TIMEOUT",
    "HEALTH_INCIDENTS",
    "UVC_DECODE_ERROR",
    "UVC_NO_SIGNAL",
    "UnhealthyFrame",
    "incident_for_error",
    "incident_for_health",
]

#: A frame arrived but carries no usable picture (black screen, no HDMI signal).
UVC_NO_SIGNAL = "uvc_no_signal"
#: No frame arrived within the capture budget.
CAPTURE_TIMEOUT = "capture_timeout"
#: Bytes arrived that could not be decoded into an image.
UVC_DECODE_ERROR = "uvc_decode_error"

#: ``Frame.health`` values (``UvcSource``) that mean "cannot act on this".
HEALTH_INCIDENTS: dict[str, str] = {
    "black": UVC_NO_SIGNAL,
    "decode_error": UVC_DECODE_ERROR,
}


def incident_for_health(health: str) -> str | None:
    """The incident code for a ``Frame.health`` value, or ``None`` when it is usable."""
    return HEALTH_INCIDENTS.get(str(health or ""))


def incident_for_error(exc: BaseException) -> str:
    """The incident code for a capture/perception exception.

    A timeout and a dark picture are different repairs, so the message decides between
    them; anything else is reported as no signal, which is what "the capture failed"
    looks like to the operator.
    """
    text = str(exc).lower()
    if "timed out" in text or "timeout" in text or "no frame" in text:
        return CAPTURE_TIMEOUT
    return UVC_NO_SIGNAL


class UnhealthyFrame(RuntimeError):
    """A capture that must not be treated as an observation.

    Carries the incident code so callers can report *why* rather than re-parsing prose.
    """

    def __init__(self, incident: str, detail: str = ""):
        self.incident = str(incident)
        self.detail = str(detail)
        message = f"{self.incident}: {self.detail}" if self.detail else self.incident
        super().__init__(message)
