"""Shared file-lease primitives for device and host-resource leases.

Both ``DeviceExecutionLock`` and ``ResourceLease`` need the same low-level
mechanics: a PID + process-created-at owner record, atomic JSON writes, stale
detection that never trusts a recycled PID, and Windows-tolerant unlink/replace.

Keeping them here (ADR-011 §7 PR-3a) makes the resource lease reuse the proven
device-lock behaviour instead of growing a second, subtly different copy.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import time
import uuid

from artemis.runtime.process_probe import pid_is_alive

__all__ = [
    "MALFORMED_GRACE_SECONDS",
    "atomic_write_json",
    "current_process_created_at",
    "pid_owned_by_artemis",
    "read_payload",
    "safe_replace",
    "safe_unlink",
]

#: How long a malformed/partial owner file is trusted as "someone is writing it".
MALFORMED_GRACE_SECONDS = 5.0


def current_process_created_at(pid: int | None = None) -> float:
    """The OS create time of ``pid`` (0.0 when psutil cannot answer)."""

    try:
        import psutil

        return float(psutil.Process(os.getpid() if pid is None else pid).create_time())
    except Exception:  # psutil missing / process gone: callers treat 0 as "unknown"
        return 0.0


def pid_owned_by_artemis(pid: int, process_created_at: float) -> bool:
    """Whether an owner record still refers to a live ARTEMIS-like process.

    ``process_created_at <= 0`` marks legacy records that cannot use PID-reuse
    protection; those are only trusted when the process looks like an ARTEMIS
    runner. This mirrors ``DeviceExecutionLock._owner_is_alive`` exactly.
    """

    if pid <= 0:
        return False
    if process_created_at > 0:
        return pid_is_alive(pid, process_created_at)
    try:
        import psutil

        process = psutil.Process(pid)
        if not process.is_running():
            return False
        name = process.name().lower()
        cmdline = " ".join(process.cmdline()).lower()
        return any(key in name or key in cmdline for key in ("python", "artemis", "pytest"))
    except Exception:
        return False


def read_payload(path: Path) -> dict | None:
    """Parse one owner JSON file; malformed or unreadable files yield ``None``."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def safe_unlink(path: Path, max_retries: int = 3, delay: float = 0.02) -> bool:
    """Unlink with retries for Windows sharing violations; missing is success."""

    for attempt in range(max_retries):
        try:
            path.unlink(missing_ok=True)
            return True
        except OSError:
            if attempt < max_retries - 1:
                time.sleep(delay)
    return False


def safe_replace(src: Path, dst: Path, max_retries: int = 3, delay: float = 0.02) -> bool:
    """``os.replace`` with the same retry policy as :func:`safe_unlink`."""

    for attempt in range(max_retries):
        try:
            os.replace(src, dst)
            return True
        except OSError:
            if attempt < max_retries - 1:
                time.sleep(delay)
    return False


def atomic_write_json(path: Path, payload: dict) -> bool:
    """Write JSON via a temporary sibling + atomic replace."""

    temp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return safe_replace(temp_path, path)
    except OSError:
        return False
    finally:
        safe_unlink(temp_path)
