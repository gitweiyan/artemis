"""Several perception sources behind one source: UVC now, a camera if it goes dark.

``DeviceSession.snapshot()`` already walks its sources, but a caller that holds a single
``PerceptionSource`` (a skill, an image pipeline, a test) needs the same fallback inside
one object. This composite reports the *source name of the frame it returned*, so
telemetry can say which eye was used — a dark card and a camera view are not the same
evidence.
"""

from __future__ import annotations

from dataclasses import fields

from artemis.devices.capabilities import CapabilitySet
from artemis.perception.base import Frame, PerceptionSource

__all__ = ["CompositePerception"]


class CompositePerception:
    """Ordered sources; the first healthy frame wins."""

    def __init__(self, sources: list[PerceptionSource]):
        if not sources:
            raise ValueError("a composite perception needs at least one source")
        self.sources = list(sources)

    @property
    def capabilities(self) -> CapabilitySet:
        values = {item.name: False for item in fields(CapabilitySet)}
        for source in self.sources:
            caps = source.capabilities
            for name in values:
                values[name] = values[name] or bool(getattr(caps, name))
        return CapabilitySet(**values)

    def close(self) -> None:
        for source in self.sources:
            closer = getattr(source, "close", None)
            if callable(closer):
                closer()

    async def snapshot(self) -> Frame:
        failures: list[str] = []
        for source in self.sources:
            try:
                frame = await source.snapshot()
            except Exception as exc:  # noqa: BLE001 - preserve the source's reason
                failures.append(f"{type(source).__name__}: {exc}")
                continue
            if getattr(frame, "health", "ok") == "ok":
                return frame
            failures.append(
                f"{type(source).__name__}: frame health={getattr(frame, 'health', None)!r}"
            )
        from artemis.devices.session import DeviceSessionError

        raise DeviceSessionError(
            "no healthy perception frame: " + ("; ".join(failures) or "no sources")
        )

    async def ui_tree(self):
        for source in self.sources:
            tree = await source.ui_tree()
            if tree is not None:
                return tree
        return None

    async def ocr(self, frame: Frame):
        for source in self.sources:
            boxes = await source.ocr(frame)
            if boxes is not None:
                return boxes
        return None

    async def locate(self, frame: Frame, target):
        for source in self.sources:
            located = await source.locate(frame, target)
            if located is not None:
                return located
        return None
