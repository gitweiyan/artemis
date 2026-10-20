"""Compose perception sources and control sinks for one device session."""

from __future__ import annotations

from dataclasses import dataclass, field, fields

from artemis.devices.capabilities import CapabilitySet
from artemis.devices.geometry import ScreenGeometry
from artemis.perception.base import Frame, PerceptionSource
from artemis.control.base import ControlSink


class DeviceSessionError(RuntimeError):
    """A session cannot satisfy a requested perception or control capability."""


@dataclass
class DeviceSession:
    perception: list[PerceptionSource] = field(default_factory=list)
    control: list[ControlSink] = field(default_factory=list)
    geometry: ScreenGeometry | None = None
    #: The relative-pointer belief when a control sink drives one (iOS UVC + HID). The
    #: driver anchors it (`home`) on connect and a caller may nudge it to wake the
    #: pointer before a precheck. ``None`` for platforms with absolute control.
    pointer: object | None = None

    @property
    def capabilities(self) -> CapabilitySet:
        values = {item.name: False for item in fields(CapabilitySet)}
        for participant in [*self.perception, *self.control]:
            caps = participant.capabilities
            for name in values:
                values[name] = values[name] or bool(getattr(caps, name))
        return CapabilitySet(**values)

    async def snapshot(self) -> Frame:
        errors: list[str] = []
        for source in self.perception:
            try:
                frame = await source.snapshot()
            except Exception as exc:  # noqa: BLE001 - preserve source failures
                errors.append(f"{type(source).__name__}: {exc}")
                continue
            if frame.health == "ok":
                return frame
            errors.append(f"{type(source).__name__}: frame health={frame.health!r}")
        detail = "; ".join(errors) or "no perception sources configured"
        raise DeviceSessionError(f"no healthy perception frame: {detail}")

    def control_for(self, *, absolute: bool) -> ControlSink:
        capability = "supports_absolute_tap" if absolute else "supports_relative_pointer"
        for sink in self.control:
            if getattr(sink.capabilities, capability):
                return sink
        mode = "absolute" if absolute else "relative"
        raise DeviceSessionError(f"no {mode} control sink with {capability}")
