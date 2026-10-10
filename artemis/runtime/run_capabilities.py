"""I2 · what each lens could actually do on this run.

Measured on 2026-10-10: every run since 2026-10-09 17:11 lost both the visual step
summarizer and the step-capsule lens — a role name resolved in the wrong config
namespace — and the only trace was one WARNING per run. 18 sessions; no artifact
said a word. Pro's verdict read ``degradations.counts = {}``, and Flash writes no
verdict at all, so the capability state of a Flash run lived nowhere.

This records it beside the run's identity (``run_identity.json``), where a reader
looks: which lens was available, which was not, and why. The counter in
:mod:`artemis.runtime.degradations` rides along, so Pro's verdict carries it too.

Design split, deliberately:

* a **programming error** (forgetting the role name) raises — silently swallowing
  it is how the original defect survived;
* **I/O** never raises — a capability record must not be able to break a run.
"""

from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any

from artemis.runtime import degradations
from third_party.mobile_use.utils.logger import get_logger

logger = get_logger(__name__)

#: Written next to the session's traces, beside ``run_identity.json``.
CAPABILITIES_FILENAME = "run_capabilities.json"

_ATTR = "run_capabilities"


def _lenses(ctx: Any) -> dict[str, dict[str, Any]]:
    """The run's lens record, created on first use."""
    existing = getattr(ctx, _ATTR, None)
    if isinstance(existing, dict):
        return existing
    record: dict[str, dict[str, Any]] = {}
    try:
        setattr(ctx, _ATTR, record)
    except (AttributeError, TypeError, ValueError):
        # A context that cannot hold attributes still gets a usable, if local, record.
        pass
    return record


def record_lens(ctx: Any, role: str, *, available: bool, reason: Any = None) -> dict[str, Any]:
    """Record whether ``role``'s lens could be built on this run.

    ``role`` is a programming input: an empty name is a bug in the caller, not a
    runtime condition, and raises. Everything else is best-effort.
    """
    if not isinstance(role, str) or not role.strip():
        raise ValueError("a lens record needs the role name it belongs to")

    entry: dict[str, Any] = {
        "available": bool(available),
        "reason": None if available else (str(reason) if reason else None),
        "at": time.time(),
    }
    _lenses(ctx)[role] = entry
    if available:
        logger.info(f"Lens '{role}' is available on this run")
    else:
        logger.info(
            f"Lens '{role}' is unavailable on this run: {entry['reason'] or 'no reason given'}"
        )
        degradations.record(ctx, degradations.LENS_UNAVAILABLE, f"{role}: {entry['reason']}")
    write_capabilities(ctx)
    return entry


def capabilities(ctx: Any) -> dict[str, Any]:
    """The record as it will be written: ``{"lenses": {role: {...}}}``."""
    return {"lenses": {role: dict(entry) for role, entry in _lenses(ctx).items()}}


def unavailable_roles(ctx: Any) -> list[str]:
    """Roles whose lens is known to be unusable on this run."""
    return sorted(role for role, entry in _lenses(ctx).items() if not entry.get("available"))


def write_capabilities(ctx: Any) -> bool:
    """Flush the record into the session directory. Best-effort; never raises.

    Called on every change and once when the run ends, so the file exists even
    when every lens was fine — a missing file and "nothing to report" must not
    look the same to a reader.
    """
    engine = getattr(ctx, "data_engine", None)
    base_dir = getattr(engine, "base_dir", None)
    # ``str``/``Path`` only: a mocked engine satisfies ``os.PathLike`` and would
    # otherwise be written to as a literal directory in the working tree.
    if not isinstance(base_dir, (str, Path)):
        return False
    try:
        path = Path(base_dir) / CAPABILITIES_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(capabilities(ctx), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return True
    except OSError as exc:
        logger.debug(f"Could not write {CAPABILITIES_FILENAME}: {exc}")
        return False


__all__ = [
    "CAPABILITIES_FILENAME",
    "capabilities",
    "record_lens",
    "unavailable_roles",
    "write_capabilities",
]
