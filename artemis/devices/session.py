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


class DeviceSessionError(RuntimeError):
    """A session cannot satisfy a requested perception or control capability."""


class SessionClosedError(DeviceSessionError):
    """The session has been closed (or is closing) and cannot serve requests."""


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
    _state: SessionState = field(default=SessionState.NEW, init=False, repr=False)
    _close_errors: list[str] = field(default_factory=list, init=False, repr=False)

    @property
    def state(self) -> SessionState:
        return self._state

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
            logger.error(f"Device session failed: {reason}")
            self._state = SessionState.FAILED

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

    async def _close_participant(self, participant: Any) -> None:
        closer = getattr(participant, "aclose", None) or getattr(participant, "close", None)
        if not callable(closer):
            return
        try:
            result = closer()
            if inspect.isawaitable(result):
                await result
        except (OSError, RuntimeError, ValueError, AttributeError) as exc:
            name = type(participant).__name__
            message = f"{name}: {exc}"
            self._close_errors.append(message)
            logger.warning(f"Closing {name} failed: {exc}")

    async def close(self) -> None:
        """Release inputs, handles, then leases. Idempotent; never raises on cleanup.

        Cancellation is special: a cancelled ``aclose`` must not strand later
        participants or the host leases. We remember it, finish the cleanup, then
        re-raise so the caller still observes cancellation.
        """

        if self._state in (SessionState.CLOSING, SessionState.CLOSED):
            return
        self._state = SessionState.CLOSING
        self._close_errors.clear()
        cancelled: asyncio.CancelledError | None = None

        # Inputs first (control), then capture (perception), as ADR-003 requires.
        # Each closer runs in a task so an arbitrary Exception (TypeError, a
        # vendor exception) is captured as task.exception() and cannot stop the
        # remaining cleanup. No broad except-handler is needed.
        for participant in [*self.control, *self.perception]:
            task = asyncio.create_task(self._close_participant(participant))
            try:
                await asyncio.wait({task})
            except asyncio.CancelledError as exc:
                cancelled = exc
                task.cancel()
                await asyncio.wait({task})
            if task.cancelled():
                cancelled = asyncio.CancelledError()
                self._close_errors.append(f"{type(participant).__name__}: cancelled")
                logger.warning(f"Closing {type(participant).__name__} was cancelled; continuing")
                continue
            error = task.exception()
            if error is not None:
                self._close_errors.append(f"{type(participant).__name__}: {error}")
                logger.warning(f"Closing {type(participant).__name__} failed: {error}")

        if self.leases is not None:
            try:
                self.leases.release()
            except (OSError, RuntimeError, ValueError) as exc:
                message = f"leases: {exc}"
                self._close_errors.append(message)
                logger.warning(f"Releasing device-session leases failed: {exc}")
            finally:
                self.leases = None

        self._state = SessionState.CLOSED
        if self._close_errors:
            logger.warning(
                f"Device session closed with {len(self._close_errors)} cleanup failure(s): "
                f"{'; '.join(self._close_errors)}"
            )
        if cancelled is not None:
            raise cancelled

    async def snapshot(self) -> Frame:
        self._ensure_usable()
        errors: list[str] = []
        for source in self.perception:
            try:
                frame = await source.snapshot()
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
