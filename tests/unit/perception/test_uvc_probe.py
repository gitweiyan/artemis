"""UVC capability probes (ADR-001 PR-2c)."""

from __future__ import annotations

import asyncio

from artemis.core.capabilities import CapabilityId
from artemis.perception.base import Frame
from artemis.perception.uvc import UvcSource


class _Source(UvcSource):
    def __init__(self, *, health="ok", ocr=False, error=None):
        super().__init__(object(), ocr_engine=object() if ocr else None)
        self._health = health
        self._error = error

    async def snapshot(self) -> Frame:
        if self._error is not None:
            raise self._error
        return Frame(b"frame", 1.0, 449, 974, "uvc", health=self._health)


def test_a_healthy_frame_makes_capture_available_and_ocr_follows_the_engine():
    source = _Source(health="ok", ocr=True)
    probes = asyncio.run(source.probe_capabilities())

    assert probes[CapabilityId.FRAME_CAPTURE].available is True
    assert probes[CapabilityId.CAPTURE_HEALTH].available is True
    assert probes[CapabilityId.OCR_READ].available is True


def test_a_black_frame_is_unavailable_with_a_reason():
    source = _Source(health="black")
    probes = asyncio.run(source.probe_capabilities())

    assert probes[CapabilityId.FRAME_CAPTURE].available is False
    assert probes[CapabilityId.FRAME_CAPTURE].reason == "frame_health=black"


def test_a_capture_exception_is_a_probe_failure_not_a_crash():
    source = _Source(error=RuntimeError("capture timeout"))
    probes = asyncio.run(source.probe_capabilities())

    assert probes[CapabilityId.FRAME_CAPTURE].available is False
    assert "capture_failed" in (probes[CapabilityId.FRAME_CAPTURE].reason or "")


def test_the_uvc_manifest_declares_capture_and_optional_ocr():
    assert CapabilityId.FRAME_CAPTURE in _Source(ocr=False).capability_manifest.declared_ids
    assert CapabilityId.CAPTURE_HEALTH in _Source().capability_manifest.declared_ids
    assert CapabilityId.OCR_READ not in _Source(ocr=False).capability_manifest.declared_ids
    assert CapabilityId.OCR_READ in _Source(ocr=True).capability_manifest.declared_ids
