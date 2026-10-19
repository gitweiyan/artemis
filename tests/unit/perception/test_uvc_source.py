import asyncio
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from artemis.perception.uvc import UvcSource


def _png(color, size=(10, 10)):
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, "PNG")
    return buffer.getvalue()


class FakeCapture:
    def __init__(self, image):
        self.image = image

    def grab(self):
        return SimpleNamespace(image=self.image, width=10, height=10, captured_at=1.0)


def test_snapshot_returns_an_ok_frame():
    source = UvcSource(FakeCapture(_png((200, 200, 200))))

    frame = asyncio.run(source.snapshot())

    assert frame.health == "ok"
    assert frame.source == "uvc"
    assert frame.width == 10


def test_black_frame_is_classified_not_ok():
    source = UvcSource(FakeCapture(_png((0, 0, 0))))

    assert asyncio.run(source.snapshot()).health == "black"


def test_undecodable_frame_is_classified():
    source = UvcSource(FakeCapture(b"not an image"))

    assert asyncio.run(source.snapshot()).health == "decode_error"


def test_unavailable_methods_return_none():
    source = UvcSource(FakeCapture(_png((10, 10, 10))))

    assert asyncio.run(source.ui_tree()) is None
    assert asyncio.run(source.ocr(None)) is None
    assert asyncio.run(source.locate(None, None)) is None


def test_capture_exception_propagates():
    class Broken:
        def grab(self):
            raise RuntimeError("no signal")

    with pytest.raises(RuntimeError):
        asyncio.run(UvcSource(Broken()).snapshot())


class FakeOcr:
    def __init__(self):
        self.calls = []

    def recognize(self, image, *, width, height):
        from artemis.perception.base import TextBox

        self.calls.append((image, width, height))
        return [TextBox("闪购", (0.2, 0.1, 0.3, 0.2), 0.99)]


def test_uvc_source_delegates_ocr_when_injected():
    engine = FakeOcr()
    source = UvcSource(FakeCapture(_png((200, 200, 200))), ocr_engine=engine)
    frame = asyncio.run(source.snapshot())

    boxes = asyncio.run(source.ocr(frame))

    assert boxes[0].text == "闪购"
    assert engine.calls == [(frame.image, 10, 10)]
    assert source.capabilities.supports_ocr is True


def test_uvc_source_without_engine_returns_none():
    source = UvcSource(FakeCapture(_png((200, 200, 200))))
    assert asyncio.run(source.ocr(None)) is None
    assert source.capabilities.supports_ocr is False


def test_uvc_source_locates_text_target():
    from artemis.perception.base import Target

    source = UvcSource(FakeCapture(_png((200, 200, 200))), ocr_engine=FakeOcr())
    frame = asyncio.run(source.snapshot())

    target = asyncio.run(source.locate(frame, Target("闪购", (0, 0, 0, 0), 0, "ocr")))

    assert target is not None
    assert target.box == (0.2, 0.1, 0.3, 0.2)
