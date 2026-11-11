"""Attaching to the accessibility helper, with a deadline.

Provisioning is a chain of ``adb shell`` calls (version probe, service check,
token push). On a healthy device that is a few hundred milliseconds; on a busy one
a single command has been measured at **83 s**, and the chain then looks like a
hang — a reflex loop that waits minutes for its capture path is worse than one that
says "not now".

So the attach runs on a worker thread with a deadline. Timing out is reported as a
plain failure with the two things a reader needs (what took too long, what to do
instead), and the thread is left to finish on its own: it is a daemon, and a
late-arriving session is harmless because the caller has already moved on.

This mirrors the repository's own lesson from 2026-10-09: a slow probe is not an
absent device, and a deadline of its own beats the transport's default.
"""

from __future__ import annotations

import subprocess
import threading
import time
from typing import Any, Callable

from third_party.mobile_use.utils.logger import get_logger

logger = get_logger(__name__)

#: How long an attach may take before the caller is told to use another path.
ATTACH_DEADLINE_S = 20.0

#: What provisioning may raise: helper errors, adb transport failures, config
#: problems. ``subprocess.SubprocessError`` matters — a stalled adb raises
#: ``TimeoutExpired``, which is not an ``OSError``.
_ATTACH_ERRORS = (
    RuntimeError,
    OSError,
    subprocess.SubprocessError,
    ValueError,
    KeyError,
    AttributeError,
    ImportError,
)


class AttachTimeout(RuntimeError):
    """The helper did not finish attaching inside the deadline."""


def _default_factory(serial: str) -> Any:
    from artemis.clients.accessibility_client import AccessibilityClient

    client = AccessibilityClient(serial)
    client.connect()
    return client


def attach_client(
    serial: str,
    *,
    deadline_s: float = ATTACH_DEADLINE_S,
    factory: Callable[[str], Any] | None = None,
) -> Any:
    """Return a connected accessibility client, or raise inside ``deadline_s``."""
    build = factory or _default_factory
    outcome: dict[str, Any] = {}

    def _work() -> None:
        try:
            outcome["client"] = build(serial)
        except _ATTACH_ERRORS as exc:
            outcome["error"] = exc

    started = time.monotonic()
    worker = threading.Thread(target=_work, name="reflex-helper-attach", daemon=True)
    worker.start()
    worker.join(max(0.1, float(deadline_s)))
    if worker.is_alive():
        raise AttachTimeout(
            f"attaching to the accessibility helper on {serial} did not finish within"
            f" {deadline_s:.0f}s (adb is slow or the helper is not provisioned). Run"
            " `artemis helper status`, or use capture='screencap' which needs no helper."
        )
    if "error" in outcome:
        raise outcome["error"]
    client = outcome.get("client")
    if client is None:  # pragma: no cover - a factory that returns nothing
        raise AttachTimeout(f"attaching to the helper on {serial} produced no client")
    logger.debug(f"helper attach on {serial} took {(time.monotonic() - started):.2f}s")
    return client


__all__ = ["ATTACH_DEADLINE_S", "AttachTimeout", "attach_client"]
