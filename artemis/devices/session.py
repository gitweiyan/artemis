"""Compose perception sources and control sinks for one device session.

ADR-003 §5.4: a session owns adapters, geometry, adapter-private state and host
resource leases; it never owns a task, a plan or an Agent. Lifecycle is
``new -> ready <-> degraded -> closing -> closed`` with a terminal ``failed``.
Closing is idempotent, releases inputs before handles, then releases leases.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from enum import StrEnum
import asyncio
import inspect
from typing import TYPE_CHECKING, Any

from artemis.control.base import ControlSink
from artemis.core.capabilities import CapabilityView
from artemis.core.capability_registry import declared_view
from artemis.core.identity import DeviceDescriptor
from artemis.core.errors import DeviceFault, DeviceSessionError, SessionClosedError
from artemis.devices.capabilities import CapabilitySet
from artemis.devices.capabilities_compat import manifest_from_capability_set
from artemis.devices.geometry import ScreenGeometry
from artemis.perception.base import Frame, PerceptionSource
from third_party.mobile_use.utils.logger import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from artemis.runtime.resource_lease import LeaseSet

logger = get_logger(__name__)


class SessionState(StrEnum):
    NEW = "new"
    READY = "ready"
    DEGRADED = "degraded"
    CLOSING = "closing"
    CLOSED = "closed"
    FAILED = "failed"


class CleanupState(StrEnum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    RETRYABLE = "retryable"
    MANUAL_REQUIRED = "manual_required"
    COMPLETE = "complete"


@dataclass(frozen=True)
class SessionCloseReport:
    """Business close state and physical cleanup state for one Session."""

    session_id: str
    session_state: SessionState
    cleanup_state: CleanupState
    attempt: int = 0
    closed_participants_now: tuple[str, ...] = ()
    closed_participants_total: tuple[str, ...] = ()
    failed_participants: tuple[str, ...] = ()
    released_now: tuple[str, ...] = ()
    released_total: tuple[str, ...] = ()
    retained: tuple[str, ...] = ()
    cleanup_complete: bool = False
    retryable: bool = False
    errors: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "session_state": self.session_state.value,
            "cleanup_state": self.cleanup_state.value,
            "attempt": self.attempt,
            "closed_participants_now": list(self.closed_participants_now),
            "closed_participants_total": list(self.closed_participants_total),
            "failed_participants": list(self.failed_participants),
            "released_now": list(self.released_now),
            "released_total": list(self.released_total),
            "retained": list(self.retained),
            "cleanup_complete": self.cleanup_complete,
            "retryable": self.retryable,
            "errors": list(self.errors),
        }


@dataclass
class DeviceSession:
    perception: list[PerceptionSource] = field(default_factory=list)
    control: list[ControlSink] = field(default_factory=list)
    geometry: ScreenGeometry | None = None
    descriptor: DeviceDescriptor | None = None
    leases: LeaseSet | None = None
    session_id: str | None = None
    #: Optional evidence-backed resolver; without it the view is declaration-only.
    capability_registry: Any | None = None
    capability_probes: Any | None = None
    #: Adapter-private state (e.g. the relative-pointer belief). Kept in one bag so the
    #: generic session does not grow platform-specific fields (ADR-003 §5.4).
    adapter_state: dict[str, Any] = field(default_factory=dict)
    last_frame_id: str | None = None
    fault_source: str | None = None
    fault_reason: str | None = None
    _state: SessionState = field(default=SessionState.NEW, init=False, repr=False)
    _close_errors: list[str] = field(default_factory=list, init=False, repr=False)
    _cleanup_state: CleanupState = field(default=CleanupState.NOT_STARTED, init=False, repr=False)
    _cleanup_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _cleanup_attempt: int = field(default=0, init=False, repr=False)
    _closed_participants: set[str] = field(default_factory=set, init=False, repr=False)
    _failed_participants: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _cleanup_owners: list[tuple[str, Any]] = field(default_factory=list, init=False, repr=False)
    _released_resource_ids: set[str] = field(default_factory=set, init=False, repr=False)
    _cancel_signal: asyncio.CancelledError | None = field(default=None, init=False, repr=False)
    last_close_report: SessionCloseReport | None = field(default=None, init=False, repr=False)

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def cleanup_state(self) -> CleanupState:
        return self._cleanup_state

    def register_cleanup_owner(self, name: str, closer: Any) -> None:
        """Register an external owner (Action/MCP) in the Session cleanup barrier."""

        if not callable(closer):
            raise TypeError("cleanup owner must be callable")
        if any(existing == name for existing, _ in self._cleanup_owners):
            raise ValueError(f"cleanup owner {name!r} already registered")
        self._cleanup_owners.append((str(name), closer))

    @property
    def pointer(self) -> Any | None:
        """Deprecated read-only compatibility view (ADR-003 §5.4; remove in Phase 9)."""

        adapter = self.adapter_state.get("pointer_adapter")
        if adapter is not None:
            return getattr(adapter, "session", None)
        return self.adapter_state.get("pointer")

    @property
    def capability_view(self) -> CapabilityView:
        """Declared + probe + evidence snapshot for this session (ADR-001)."""

        manifests = []
        for participant in [*self.perception, *self.control]:
            manifest = getattr(participant, "capability_manifest", None)
            if manifest is None:
                manifest = manifest_from_capability_set(
                    participant.capabilities,
                    adapter_id=type(participant).__name__,
                )
            manifests.append(manifest)
        extension = self.adapter_state.get("platform_extension")
        extension_manifest = getattr(extension, "capabilities", None)
        if extension_manifest is not None:
            manifests.append(extension_manifest)
        if self.capability_registry is None or self.descriptor is None:
            return declared_view(manifests)
        return self.capability_registry.resolve(
            descriptor=self.descriptor,
            manifests=manifests,
            probes=self.capability_probes,
        )

    @property
    def capabilities(self) -> CapabilitySet:
        values = {item.name: False for item in fields(CapabilitySet)}
        for participant in [*self.perception, *self.control]:
            caps = participant.capabilities
            for name in values:
                values[name] = values[name] or bool(getattr(caps, name))
        return CapabilitySet(**values)

    def open(self) -> DeviceSession:
        """Mark the session usable; idempotent. Handles are opened by the composition root."""

        if self._state is SessionState.FAILED:
            raise DeviceSessionError(
                "a failed device session is terminal; create a new session instead"
            )
        if self._state in (SessionState.CLOSING, SessionState.CLOSED):
            raise SessionClosedError("cannot open a closing/closed device session")
        if self._state in (SessionState.READY, SessionState.DEGRADED):
            return self
        self._state = SessionState.READY
        return self

    def mark_degraded(self, reason: str) -> None:
        if self._state is SessionState.READY:
            logger.warning(f"Device session degraded: {reason}")
            self._state = SessionState.DEGRADED

    def mark_failed(self, reason: str) -> None:
        if self._state not in (SessionState.CLOSED, SessionState.CLOSING):
            if self.fault_reason is None:
                self.fault_reason = str(reason)
            logger.error(f"Device session failed: {reason}")
            self._state = SessionState.FAILED

    def record_fault(self, fault: DeviceFault) -> None:
        """Record the first classified fatal device fault and fail the session."""

        if not fault.fatal:
            return
        if self.fault_source is None:
            self.fault_source = fault.source
        self.mark_failed(f"{fault.kind.value}: {fault}")

    def health(self) -> SessionState:
        return self._state

    def _ensure_usable(self) -> None:
        if self._state is SessionState.FAILED:
            raise DeviceSessionError("device session is failed and cannot serve requests")
        if self._state in (SessionState.CLOSING, SessionState.CLOSED):
            raise SessionClosedError("device session is closed")
        if self._state is SessionState.NEW:
            self.open()

    async def probe_capabilities(self) -> dict[str, Any]:
        """Ask each adapter for a non-destructive availability probe (ADR-001)."""

        self._ensure_usable()
        probes: dict[str, Any] = {}
        for participant in [*self.perception, *self.control]:
            prober = getattr(participant, "probe_capabilities", None)
            if not callable(prober):
                continue
            try:
                result = prober()
                if inspect.isawaitable(result):
                    result = await result
            except (OSError, RuntimeError, ValueError, AttributeError) as exc:
                logger.warning(f"Capability probe failed on {type(participant).__name__}: {exc}")
                continue
            if result:
                probes.update(result)
        self.capability_probes = probes
        return probes

    async def _close_participant(self, participant: Any) -> str | None:
        closer = getattr(participant, "aclose", None) or getattr(participant, "close", None)
        if not callable(closer):
            return None
        try:
            result = closer()
            if inspect.isawaitable(result):
                await result
        except (OSError, RuntimeError, ValueError, AttributeError) as exc:
            name = type(participant).__name__
            message = f"{name}: {exc}"
            logger.warning(f"Closing {name} failed: {exc}")
            return message
        return None

    def _participant_entries(self) -> list[tuple[str, Any]]:
        participants = [*self.control, *self.perception]
        return [
            (f"{index}:{type(participant).__name__}", participant)
            for index, participant in enumerate(participants)
        ]

    def _report(self) -> SessionCloseReport:
        retained = tuple(getattr(self.leases, "resource_ids", ())) if self.leases is not None else ()
        return SessionCloseReport(
            session_id=str(self.session_id or ""),
            session_state=self._state,
            cleanup_state=self._cleanup_state,
            attempt=self._cleanup_attempt,
            closed_participants_now=(),
            closed_participants_total=tuple(sorted(self._closed_participants)),
            failed_participants=tuple(sorted(self._failed_participants)),
            released_now=(),
            released_total=tuple(sorted(self._released_resource_ids)),
            retained=retained,
            cleanup_complete=self._cleanup_state is CleanupState.COMPLETE,
            retryable=self._cleanup_state is CleanupState.RETRYABLE,
            errors=tuple(self._close_errors),
        )

    async def _close_cleanup_owners(self) -> tuple[list[str], list[str]]:
        closed_now: list[str] = []
        for name, closer in self._cleanup_owners:
            participant_id = f"external:{name}"
            if participant_id in self._closed_participants:
                continue
            try:
                result = closer()
                if inspect.isawaitable(result):
                    await result
            except (OSError, RuntimeError, ValueError, AttributeError) as exc:
                message = f"{participant_id}: {exc}"
                self._failed_participants[participant_id] = message
                self._close_errors.append(message)
                continue
            self._failed_participants.pop(participant_id, None)
            self._closed_participants.add(participant_id)
            closed_now.append(participant_id)
        return closed_now, list(self._failed_participants)

    async def _close_participants(self, entries: list[tuple[str, Any]]) -> tuple[list[str], list[str]]:
        cancelled: asyncio.CancelledError | None = None
        closed_now: list[str] = []
        current = asyncio.current_task()
        for participant_id, participant in entries:
            if participant_id in self._closed_participants:
                continue
            task = asyncio.create_task(self._close_participant(participant))
            while not task.done():
                try:
                    await asyncio.wait({task})
                except asyncio.CancelledError as exc:
                    cancelled = exc
                    if current is not None and hasattr(current, "uncancel"):
                        current.uncancel()
            if task.cancelled():
                cancelled = cancelled or asyncio.CancelledError()
                message = f"{participant_id}: cancelled"
                self._failed_participants[participant_id] = message
                self._close_errors.append(message)
                continue
            error = task.exception()
            if error is not None:
                message = f"{participant_id}: {error}"
                self._failed_participants[participant_id] = message
                self._close_errors.append(message)
                continue
            result = task.result()
            if result:
                self._failed_participants[participant_id] = result
                self._close_errors.append(result)
                continue
            self._failed_participants.pop(participant_id, None)
            self._closed_participants.add(participant_id)
            closed_now.append(participant_id)
        if cancelled is not None:
            self._cancel_signal = cancelled
        return closed_now, list(self._failed_participants)

    async def _cleanup_leases(self) -> tuple[list[str], list[str], list[str]]:
        released_now: list[str] = []
        errors: list[str] = []
        if self.leases is None:
            return released_now, errors, []
        try:
            report = getattr(self.leases, "release_with_report", None)
            if callable(report):
                result = report()
                released_now = list(getattr(result, "released", ()))
                for resource_id, message in getattr(result, "errors", ()):
                    errors.append(f"leases: {resource_id}: {message}")
                residual = list(getattr(result, "residual", ()))
            else:
                self.leases.release()
                residual = []
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - cleanup must not strand the session
            errors.append(f"leases: {exc}")
            residual = list(getattr(self.leases, "resource_ids", ()))
        self._released_resource_ids.update(released_now)
        remaining = getattr(self.leases, "resource_ids", None)
        if not errors and not residual and not remaining:
            self.leases = None
        return released_now, errors, residual

    async def _run_cleanup(self, *, retry: bool = False) -> SessionCloseReport:
        owners_closed, _ = await self._close_cleanup_owners()
        entries = self._participant_entries()
        participants_closed, failed = await self._close_participants(entries)
        closed_now = [*owners_closed, *participants_closed]
        if failed:
            self._cleanup_state = CleanupState.RETRYABLE
            self._state = SessionState.CLOSED
            report = self._report()
            self.last_close_report = report
            return report
        released_now, lease_errors, residual = await self._cleanup_leases()
        self._close_errors.extend(lease_errors)
        if lease_errors or residual:
            self._cleanup_state = CleanupState.RETRYABLE
        else:
            self._cleanup_state = CleanupState.COMPLETE
            self._cleanup_attempt = max(1, self._cleanup_attempt)
        self._state = SessionState.CLOSED
        report = SessionCloseReport(
            session_id=str(self.session_id or ""),
            session_state=self._state,
            cleanup_state=self._cleanup_state,
            attempt=self._cleanup_attempt,
            closed_participants_now=tuple(closed_now),
            closed_participants_total=tuple(sorted(self._closed_participants)),
            failed_participants=tuple(sorted(failed)),
            released_now=tuple(released_now),
            released_total=tuple(sorted(self._released_resource_ids)),
            retained=tuple(getattr(self.leases, "resource_ids", ())) if self.leases is not None else (),
            cleanup_complete=self._cleanup_state is CleanupState.COMPLETE,
            retryable=self._cleanup_state is CleanupState.RETRYABLE,
            errors=tuple(self._close_errors),
        )
        self.last_close_report = report
        return report

    async def close(self) -> SessionCloseReport:
        """Close business operations and run one cleanup attempt.

        ``CLOSED`` means no new work is accepted. Physical cleanup is reported by
        ``SessionCloseReport``; a retry uses :meth:`retry_cleanup`.
        """
        async with self._cleanup_lock:
            if self._state is SessionState.CLOSED:
                return self.last_close_report or self._report()
            if self._state is SessionState.CLOSING:
                return self.last_close_report or self._report()
            self._state = SessionState.CLOSING
            self._cleanup_state = CleanupState.IN_PROGRESS
            self._cleanup_attempt += 1
            self._close_errors.clear()
            self._cancel_signal = None
            try:
                report = await self._run_cleanup()
            except asyncio.CancelledError as exc:
                self._state = SessionState.CLOSED
                self._cleanup_state = CleanupState.RETRYABLE
                report = self._report()
                self.last_close_report = report
                raise
            if self._cancel_signal is not None:
                cancelled = self._cancel_signal
                self._cancel_signal = None
                raise cancelled
            if self._close_errors:
                logger.warning(
                    f"Device session closed with {len(self._close_errors)} cleanup failure(s): "
                    f"{'; '.join(self._close_errors)}"
                )
            return report

    async def retry_cleanup(self) -> SessionCloseReport:
        """Retry only failed cleanup work; never reopens the Session."""
        async with self._cleanup_lock:
            if self._state is not SessionState.CLOSED:
                raise DeviceSessionError("cleanup retry requires a closed session")
            if self._cleanup_state is CleanupState.COMPLETE:
                return self.last_close_report or self._report()
            if self._cleanup_state not in (
                CleanupState.RETRYABLE,
                CleanupState.MANUAL_REQUIRED,
            ):
                raise DeviceSessionError("cleanup is not retryable")
            self._cleanup_state = CleanupState.IN_PROGRESS
            self._cleanup_attempt += 1
            self._cancel_signal = None
            report = await self._run_cleanup(retry=True)
            if self._cancel_signal is not None:
                cancelled = self._cancel_signal
                self._cancel_signal = None
                raise cancelled
            return report

    async def snapshot(self) -> Frame:
        self._ensure_usable()
        errors: list[str] = []
        for source in self.perception:
            try:
                frame = await source.snapshot()
            except DeviceFault as exc:
                self.record_fault(exc)
                raise
            except Exception as exc:  # noqa: BLE001 - preserve source failures
                errors.append(f"{type(source).__name__}: {exc}")
                continue
            if frame.health == "ok":
                self.last_frame_id = getattr(frame, "frame_id", None) or self.last_frame_id
                return frame
            errors.append(f"{type(source).__name__}: frame health={frame.health!r}")
        detail = "; ".join(errors) or "no perception sources configured"
        raise DeviceSessionError(f"no healthy perception frame: {detail}")

    def validate_target(self, target: Any) -> Any:
        """Reject a target computed on a different frame (ADR-002 D4)."""

        target_frame = getattr(target, "frame_id", None)
        if target_frame and self.last_frame_id and target_frame != self.last_frame_id:
            raise DeviceSessionError(
                f"stale_frame: target frame {target_frame!r} != current {self.last_frame_id!r}"
            )
        return target

    def control_for(self, *, absolute: bool) -> ControlSink:
        self._ensure_usable()
        capability = "supports_absolute_tap" if absolute else "supports_relative_pointer"
        for sink in self.control:
            if getattr(sink.capabilities, capability):
                return sink
        mode = "absolute" if absolute else "relative"
        raise DeviceSessionError(f"no {mode} control sink with {capability}")

    async def __aenter__(self) -> DeviceSession:
        return self.open()

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.close()
