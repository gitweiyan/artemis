"""Cross-process leases for exclusive host resources (UVC, serial, camera, helper).

Semantics are defined by ADR-003; mechanism by ADR-011:

- one owner per ``resource_id`` (already host-scoped, e.g. ``uvc:host:key/capture``);
- owner identity is PID + process create time + token (stale PID reuse is not trusted);
- live owners are never preempted by heartbeat/TTL; only death, PID reuse, explicit
  release or ``cleanup_stale()`` may reclaim a file;
- polling with a timeout/cancel event, no global FIFO;
- ``LeaseSet`` acquires in sorted order and rolls back in reverse on failure.

The lease is advisory: it excludes other ARTEMIS processes, not external apps that
open the same camera or serial port.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import uuid
from typing import Any, Iterable, Mapping

from artemis.config.paths import get_temp_dir
from artemis.runtime import lease_primitives
from third_party.mobile_use.utils.logger import get_logger

logger = get_logger(__name__)

__all__ = [
    "LeaseSet",
    "ResourceBusyError",
    "ResourceLease",
    "ResourceLeaseOwner",
    "cleanup_stale",
    "get_active_leases",
    "lease_directory",
    "leases_enabled",
]

ENV_LEASE_DIR = "ARTEMIS_RESOURCE_LEASE_DIR"
ENV_LEASE_ENABLED = "ARTEMIS_RESOURCE_LEASE"
SCHEMA_VERSION = 1
DEFAULT_TIMEOUT_S = 10.0
_POLL_SECONDS = 0.1
_METADATA_WHITELIST = frozenset({"local_port", "path", "device", "serial", "socket"})
_METADATA_MAX_LENGTH = 128


def leases_enabled(env: Mapping[str, str] | None = None) -> bool:
    """The ADR-011 rollback switch; enabled unless explicitly disabled."""

    values = os.environ if env is None else env
    value = (values.get(ENV_LEASE_ENABLED) or "1").strip().lower()
    return value not in {"0", "false", "off", "no"}


def lease_directory(lease_dir: Path | str | None = None) -> Path:
    """The lease directory: explicit argument, env override, then temp default."""

    if lease_dir is not None:
        return Path(lease_dir)
    override = (os.environ.get(ENV_LEASE_DIR) or "").strip()
    if override:
        return Path(override).expanduser()
    return get_temp_dir("resource-leases")


def _lease_path(resource_id: str, directory: Path) -> Path:
    digest = hashlib.sha256(resource_id.encode("utf-8")).hexdigest()[:24]
    return directory / f"resource-{digest}.lock"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _metadata(values: Mapping[str, str] | None) -> Mapping[str, str]:
    if not values:
        return {}
    unknown = set(values) - _METADATA_WHITELIST
    if unknown:
        raise ValueError(
            f"resource lease metadata only accepts diagnostic keys "
            f"{sorted(_METADATA_WHITELIST)}; got {sorted(unknown)}"
        )
    cleaned: dict[str, str] = {}
    for key, value in values.items():
        text = str(value).strip()
        if not text or len(text) > _METADATA_MAX_LENGTH:
            raise ValueError(f"metadata[{key!r}] must be 1..{_METADATA_MAX_LENGTH} characters")
        cleaned[key] = text
    return cleaned


@dataclass(frozen=True)
class ResourceLeaseOwner:
    pid: int
    process_created_at: float
    token: str
    resource_id: str
    host_id: str
    description: str
    acquired_at: str
    session_id: str | None = None
    heartbeat_at: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> ResourceLeaseOwner:
        return cls(
            pid=int(payload["pid"]),
            process_created_at=float(payload["process_created_at"]),
            token=str(payload["token"]),
            resource_id=str(payload["resource_id"]),
            host_id=str(payload.get("host_id", "")),
            description=str(payload.get("description", "resource lease")),
            acquired_at=str(payload.get("acquired_at", "unknown")),
            session_id=(
                str(payload["session_id"]) if payload.get("session_id") is not None else None
            ),
            heartbeat_at=(
                str(payload["heartbeat_at"]) if payload.get("heartbeat_at") is not None else None
            ),
            metadata=dict(payload.get("metadata") or {}),
        )

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "resource_id": self.resource_id,
            "host_id": self.host_id,
            "pid": self.pid,
            "process_created_at": self.process_created_at,
            "token": self.token,
            "description": self.description,
            "acquired_at": self.acquired_at,
            "metadata": dict(self.metadata),
        }
        if self.session_id:
            payload["session_id"] = self.session_id
        if self.heartbeat_at:
            payload["heartbeat_at"] = self.heartbeat_at
        return payload

    def summary(self) -> str:
        return f"PID {self.pid} ({self.description}, since {self.acquired_at})"


class ResourceBusyError(RuntimeError):
    """Another ARTEMIS owner already holds ``resource_id``."""

    def __init__(self, resource_id: str, owner: ResourceLeaseOwner | None = None):
        detail = f" by {owner.summary()}" if owner is not None else ""
        super().__init__(f"Host resource {resource_id!r} is busy{detail}")
        self.resource_id = resource_id
        self.owner = owner


@dataclass
class _Hold:
    owner: ResourceLeaseOwner
    refs: int = 1


_HELD: dict[Path, _Hold] = {}
_HELD_LOCK = threading.Lock()


def _same_owner(owner: ResourceLeaseOwner, *, pid: int, token: str, session_id: str | None) -> bool:
    if owner.pid != pid:
        return False
    if session_id and owner.session_id:
        return owner.session_id == session_id
    return owner.token == token


def _read_owner(path: Path) -> ResourceLeaseOwner | None:
    payload = lease_primitives.read_payload(path)
    if payload is None:
        return None
    try:
        return ResourceLeaseOwner.from_payload(payload)
    except (KeyError, TypeError, ValueError):
        return None


def _remove_if_stale(path: Path, *, owner: ResourceLeaseOwner | None) -> bool:
    """Remove a dead or too-old malformed lease; never a live owner."""

    if owner is not None:
        if lease_primitives.pid_owned_by_artemis(owner.pid, owner.process_created_at):
            return False
        return lease_primitives.safe_unlink(path)
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return True
    if age < lease_primitives.MALFORMED_GRACE_SECONDS:
        return False
    return lease_primitives.safe_unlink(path)


class ResourceLease:
    """One exclusive host resource owned by this process/session."""

    def __init__(
        self,
        resource_id: str,
        *,
        host_id: str = "",
        description: str = "resource lease",
        session_id: str | None = None,
        token: str | None = None,
        lease_dir: Path | str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        text = str(resource_id or "").strip()
        if not text:
            raise ValueError("resource_id must not be empty")
        self.resource_id = text
        self.host_id = str(host_id or "")
        self.description = str(description or "resource lease")
        self.session_id = str(session_id) if session_id else None
        self.token = str(token) if token else uuid.uuid4().hex
        self.directory = lease_directory(lease_dir)
        self.path = _lease_path(self.resource_id, self.directory)
        self.metadata = dict(_metadata(metadata))
        self._owner: ResourceLeaseOwner | None = None

    @property
    def owner(self) -> ResourceLeaseOwner | None:
        return self._owner

    def _new_owner(self) -> ResourceLeaseOwner:
        return ResourceLeaseOwner(
            pid=os.getpid(),
            process_created_at=lease_primitives.current_process_created_at(),
            token=self.token,
            resource_id=self.resource_id,
            host_id=self.host_id,
            description=self.description,
            acquired_at=_now_iso(),
            session_id=self.session_id,
            heartbeat_at=_now_iso(),
            metadata=self.metadata,
        )

    def _register(self, owner: ResourceLeaseOwner) -> None:
        self._owner = owner

    def acquire(
        self,
        *,
        blocking: bool = True,
        timeout: float | None = DEFAULT_TIMEOUT_S,
        cancel_event: Any | None = None,
    ) -> ResourceLease:
        if self._owner is not None:
            return self

        self.directory.mkdir(parents=True, exist_ok=True)
        deadline = None if timeout is None else time.monotonic() + max(0.0, float(timeout))
        while True:
            with _HELD_LOCK:
                hold = _HELD.get(self.path)
                if hold is not None:
                    if _same_owner(
                        hold.owner,
                        pid=os.getpid(),
                        token=self.token,
                        session_id=self.session_id,
                    ):
                        hold.refs += 1
                        self._register(hold.owner)
                        return self
                    busy_owner: ResourceLeaseOwner | None = hold.owner
                else:
                    busy_owner = self._try_create()
                    if self._owner is not None:
                        return self

            if not blocking:
                raise ResourceBusyError(self.resource_id, busy_owner)
            if cancel_event is not None and cancel_event.is_set():
                raise ResourceBusyError(self.resource_id, busy_owner)
            if deadline is not None and time.monotonic() >= deadline:
                raise ResourceBusyError(self.resource_id, busy_owner)
            time.sleep(_POLL_SECONDS)

    def _try_create(self) -> ResourceLeaseOwner | None:
        """Create the lease file; returns the blocking owner when it cannot."""

        owner = _read_owner(self.path)
        if self.path.exists():
            if owner is not None and not lease_primitives.pid_owned_by_artemis(
                owner.pid, owner.process_created_at
            ):
                lease_primitives.safe_unlink(self.path)
            elif owner is None:
                if not _remove_if_stale(self.path, owner=None):
                    return None
            else:
                if _same_owner(
                    owner,
                    pid=os.getpid(),
                    token=self.token,
                    session_id=self.session_id,
                ):
                    _HELD[self.path] = _Hold(owner=owner, refs=1)
                    self._register(owner)
                    return None
                return owner

        candidate = self._new_owner()
        payload = json.dumps(candidate.to_payload(), ensure_ascii=False).encode("utf-8")
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return _read_owner(self.path)
        try:
            os.write(fd, payload)
            os.fsync(fd)
        except OSError:
            os.close(fd)
            lease_primitives.safe_unlink(self.path)
            raise
        else:
            os.close(fd)

        hold = _HELD.get(self.path)
        if hold is not None:
            # Another thread in this process won the file race.
            hold.refs += 1
            self._register(hold.owner)
            return None
        _HELD[self.path] = _Hold(owner=candidate, refs=1)
        self._register(candidate)
        logger.info(
            f"Resource lease acquired: {self.resource_id} ({self.description}, pid={candidate.pid})"
        )
        return None

    def release(self) -> None:
        if self._owner is None:
            return
        with _HELD_LOCK:
            hold = _HELD.get(self.path)
            if hold is None:
                self._owner = None
                return
            if not _same_owner(
                hold.owner,
                pid=os.getpid(),
                token=self.token,
                session_id=self.session_id,
            ):
                logger.warning(
                    f"Refusing to release a resource lease owned by "
                    f"{hold.owner.summary()}: {self.resource_id}"
                )
                self._owner = None
                return
            hold.refs -= 1
            remaining = hold.refs
        if remaining > 0:
            self._owner = None
            return

        with _HELD_LOCK:
            hold = _HELD.get(self.path)
            if hold is None or hold.refs > 0:
                self._owner = None
                return
            _HELD.pop(self.path, None)

        current = _read_owner(self.path)
        if current is None or _same_owner(
            current, pid=os.getpid(), token=self.token, session_id=self.session_id
        ):
            lease_primitives.safe_unlink(self.path)
        self._owner = None
        logger.info(f"Resource lease released: {self.resource_id}")

    def touch(self) -> bool:
        if self._owner is None:
            return False
        updated = replace(self._owner, heartbeat_at=_now_iso())
        if not lease_primitives.atomic_write_json(self.path, updated.to_payload()):
            return False
        with _HELD_LOCK:
            hold = _HELD.get(self.path)
            if hold is not None and hold.owner.token == updated.token:
                hold.owner = updated
        self._owner = updated
        return True

    def __enter__(self) -> ResourceLease:
        return self.acquire()

    def __exit__(self, *exc_info: Any) -> None:
        self.release()


class LeaseSet:
    """Acquire several resources in a deterministic order and release in reverse."""

    def __init__(
        self,
        *,
        host_id: str = "",
        description: str = "device session",
        session_id: str | None = None,
        lease_dir: Path | str | None = None,
        metadata: Mapping[str, Mapping[str, str]] | None = None,
    ) -> None:
        self.host_id = host_id
        self.description = description
        self.session_id = session_id
        self.lease_dir = lease_dir
        self.metadata = metadata or {}
        self._leases: list[ResourceLease] = []

    @property
    def resource_ids(self) -> tuple[str, ...]:
        return tuple(lease.resource_id for lease in self._leases)

    def acquire(
        self,
        resource_ids: Iterable[str],
        *,
        blocking: bool = True,
        timeout: float | None = DEFAULT_TIMEOUT_S,
        cancel_event: Any | None = None,
    ) -> LeaseSet:
        wanted = sorted({str(resource_id).strip() for resource_id in resource_ids if resource_id})
        if not wanted:
            return self
        acquired: list[ResourceLease] = []
        completed = False
        try:
            for resource_id in wanted:
                lease = ResourceLease(
                    resource_id,
                    host_id=self.host_id,
                    description=self.description,
                    session_id=self.session_id,
                    lease_dir=self.lease_dir,
                    metadata=self.metadata.get(resource_id),
                )
                lease.acquire(blocking=blocking, timeout=timeout, cancel_event=cancel_event)
                acquired.append(lease)
            completed = True
        finally:
            if not completed:
                for lease in reversed(acquired):
                    lease.release()
        self._leases = acquired
        return self

    def release(self) -> None:
        """Release every lease even if one owner fails; surface the first error after."""

        errors: list[Exception] = []
        leases = list(self._leases)
        self._leases.clear()
        for lease in reversed(leases):
            try:
                lease.release()
            except (OSError, RuntimeError, ValueError) as exc:
                errors.append(exc)
                logger.warning(f"Releasing resource lease {lease.resource_id} failed: {exc}")
        if errors:
            raise errors[0]

    def __enter__(self) -> LeaseSet:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.release()


def get_active_leases(
    lease_dir: Path | str | None = None,
) -> dict[str, ResourceLeaseOwner]:
    """Live resource leases by ``resource_id`` (for doctor/diagnostics)."""

    directory = lease_directory(lease_dir)
    if not directory.exists():
        return {}
    active: dict[str, ResourceLeaseOwner] = {}
    for path in sorted(directory.glob("resource-*.lock")):
        owner = _read_owner(path)
        if owner is None:
            continue
        if lease_primitives.pid_owned_by_artemis(owner.pid, owner.process_created_at):
            active[owner.resource_id] = owner
    return active


def cleanup_stale(lease_dir: Path | str | None = None) -> int:
    """Remove dead or abandoned lease files; returns the number removed."""

    directory = lease_directory(lease_dir)
    if not directory.exists():
        return 0
    removed = 0
    for path in sorted(directory.glob("resource-*.lock")):
        if not path.exists():
            continue
        owner = _read_owner(path)
        if _remove_if_stale(path, owner=owner):
            removed += 1
    return removed
